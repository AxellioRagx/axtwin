import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type {
  AssertionsDoc,
  CastSummary,
  ImprintList,
  ImprintProvenance,
} from "../src/api/types";
import { TestApp } from "../src/routes";

const CATALOG: ImprintList = {
  imprints: [
    {
      id: "corpapcn-golden",
      hosts: 156,
      segments: 32,
      as_of: "2026-09-05T00:00:00Z",
      mean_confidence: 0.72,
      has_provenance: true,
      confidence_source: "sidecar",
    },
    {
      id: "corpapcn-twin",
      hosts: 138,
      segments: 27,
      as_of: "",
      mean_confidence: 0,
      has_provenance: false,
      confidence_source: "absent",
    },
  ],
  skipped: [{ id: "half-cast", reason: "missing half-cast.yaml" }],
};

const CAST: CastSummary = {
  id: "corpapcn-golden",
  bundle_dir: "/bundles/corpapcn-golden",
  twin_config_id: "corpapcn-golden",
  description: "the golden candidate",
  pit_id: "pit-1",
  bundle_schema: "axr-topology/1.5",
  topology_schema_version: "1.3",
  code_fingerprint: "abc123",
  params: {},
  deploy_min_occupancy: 0.8,
  deploy_min_occupancy_source: "declared",
  segments: 32,
  node_specs: 62,
  vms: 138,
  hosts: 156,
  conduits: 22,
  // The roster behind the count (R5): 22 entries, so the fixture never
  // disagrees with itself the way the wire never may.
  conduit_roster: Array.from({ length: 22 }, (_, i) => ({ from: `seg-a-${i}`, to: `seg-b-${i}` })),
  routers: [{ name: "gw", segments: ["seg-a-0"] }],
  observed_nodes: 786,
  observed_edges: 1437,
  observed_segments: 32,
  census: {},
  has_provenance: true,
  has_topology_export: true,
  has_assertions: false,
  has_layout: false,
  provenance_schema: "axr-cast-provenance/1",
  provenance_path: "/bundles/corpapcn-golden/corpapcn-golden.provenance.json",
  sidecar_shape: "canonical",
  evidence_source: "sidecar fields",
  warnings: [],
};

const PROVENANCE: ImprintProvenance = {
  imprint_id: "corpapcn-golden",
  rows: [
    {
      field: "nodes[0].role",
      signal: "ir_role",
      evidence_class: "observed",
      confidence: 0.9,
      evidence: "seen on the wire",
      has_confidence: true,
      source: null,
      note: null,
      overrides: null,
    },
    {
      field: "traffic_profile.assignments[0].seed",
      signal: "sha256",
      evidence_class: "compiler",
      confidence: 0,
      evidence: "deterministic hash",
      has_confidence: false,
      source: null,
      note: null,
      overrides: null,
    },
  ],
  sidecar_shape: "canonical",
  sidecar_schema: "axr-cast-provenance/1",
  evidence_source: "sidecar fields",
  code_fingerprint: "abc123",
  population: { suppressed: [] },
  warnings: [],
};

const ASSERTIONS: AssertionsDoc = {
  imprint_id: "corpapcn-golden",
  path: "/bundles/corpapcn-golden/corpapcn-golden.assertions.yaml",
  exists: true,
  etag: "etag-1",
  schema: "axr-assertions/1",
  imprint: "corpapcn-golden",
  as_of: "2026-09-08T00:00:00Z",
  source: "operator: tester",
  add: [],
  override: [],
  suppress: [{ key: "address:10.1.10.222", note: "decommissioned" }],
  warnings: [],
};

/**
 * A full `ConsoleApi`, typed rather than cast (S10). `as ConsoleApi` would
 * hide a method the interface gains later -- the double would keep compiling
 * and the shell would call `undefined` at runtime, which is exactly the class
 * of drift a hand-written seam is prone to.
 */
function fakeApi(
  calls: string[] = [],
  overrides: Partial<ConsoleApi> = {},
): ConsoleApi {
  const unused = (name: string) => () =>
    Promise.reject(new Error(`${name} not used by this test`));
  const api: ConsoleApi = {
    listImprints: () => Promise.resolve(CATALOG),
    getImprint: unused("getImprint"),
    getProvenance: (id: string) => {
      calls.push(`provenance:${id}`);
      return Promise.resolve(PROVENANCE);
    },
    getTopology: unused("getTopology"),
    getCast: (id: string) => {
      calls.push(`cast:${id}`);
      return Promise.resolve({ ...CAST, id, twin_config_id: id });
    },
    getAssertions: (id: string) => {
      calls.push(`assertions:${id}`);
      // The second bundle has no assertions file yet -- the ordinary state
      // of every imprint before the first save.
      return Promise.resolve(
        id === "corpapcn-golden"
          ? ASSERTIONS
          : { ...ASSERTIONS, imprint_id: id, imprint: id, exists: false,
              etag: null, suppress: [] },
      );
    },
    putAssertions: unused("putAssertions"),
    getLayout: unused("getLayout"),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
  return { ...api, ...overrides };
}

/** The value of one `<dt>/<dd>` fact pair in a pane -- the panes repeat
 *  labels (a CIDR is in the breadcrumb, the map heading and the inspector),
 *  so a global text query would match three nodes and prove nothing. */
function factValue(container: HTMLElement, name: string): string {
  const dt = [...container.querySelectorAll("dt")].find(
    (node) => node.textContent === name,
  );
  if (!dt) throw new Error(`no fact named ${name}`);
  return dt.nextElementSibling?.textContent ?? "";
}

describe("the three-pane shell", () => {
  it("lists the catalog, INCLUDING what the tolerant listing skipped", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    const outline = await screen.findByLabelText("Imprints");
    expect(within(outline).getByText("corpapcn-golden")).toBeDefined();
    expect(within(outline).getByText("corpapcn-twin")).toBeDefined();
    // A bundle missing with no explanation is the failure `skipped` exists
    // to prevent -- it is only prevented if a pane renders it.
    expect(within(outline).getByText("half-cast")).toBeDefined();
    expect(within(outline).getByText(/missing half-cast.yaml/)).toBeDefined();
    // Fix round 2: said as "could not be read", in plain words -- never "Skipped".
    expect(within(outline).getByText("Could not be read")).toBeDefined();
    expect(within(outline).queryByText("Skipped")).toBeNull();
  });

  it("the right pane names the view in words, not the grammar's token", async () => {
    render(<TestApp client={fakeApi()} url="/design" />);
    const inspector = await screen.findByLabelText("Inspector");
    expect(factValue(inspector, "showing")).toBe("the list of imprints");
    expect(inspector.textContent).not.toMatch(/levelcatalog/);
  });

  it("shows the cast's live numbers in the map pane", async () => {
    // R1: the numbers live in the summary strip, which is collapsed by
    // default -- `?summary=1` is the URL state that opens it.
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden?summary=1" />);
    const numbers = await screen.findByTestId("cast-numbers");
    expect(numbers.textContent).toContain("32");   // segments
    expect(numbers.textContent).toContain("138");  // VMs
    expect(numbers.textContent).toContain("786");  // observed nodes
    expect(numbers.textContent).toContain("1437"); // observed edges
    expect(numbers.textContent).toContain("0.8");  // the deploy gate
    expect(numbers.textContent).toContain("declared");
  });

  it("counts the evidence mix by class", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden?summary=1" />);
    const mix = await screen.findByTestId("evidence-mix");
    const rows = [...mix.querySelectorAll("li")].map((li) => li.textContent);
    expect(rows).toContain("observed1");
    expect(rows).toContain("compiler1");
    expect(rows).toContain("imputed0");
  });

  it("reads the level and the scope out of the URL", async () => {
    render(
      <TestApp
        client={fakeApi()}
        url="/design/corpapcn-golden/segments/10.80.20.0-24?ghosts=1&class=imputed"
      />,
    );
    await screen.findByTestId("summary-bar");
    // The inspector renders the PARSED state, which is how a reader checks a
    // filter was understood rather than ignored.
    const inspector = screen.getByLabelText("Inspector");
    expect(factValue(inspector, "showing")).toBe("one segment");
    expect(factValue(inspector, "segment")).toBe("10.80.20.0/24");
    expect(factValue(inspector, "ghosts")).toBe("shown");
    expect(factValue(inspector, "class")).toBe("imputed");
    expect(factValue(inspector, "occupancy_lt")).toBe("\u2014");
  });

  it("REPORTS a filter value it could not use", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden?class=nonsense" />);
    expect(
      await screen.findByText(/class=nonsense: not one of the six/),
    ).toBeDefined();
  });

  it("says a route is unrecognized instead of rendering an empty canvas", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden/nonsense" />);
    expect(await screen.findByText("No such view")).toBeDefined();
  });

  it("loads the assertions file into the edit store as SAVED records", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    await waitFor(() => {
      expect(screen.getByText("etag-1")).toBeDefined();
    });
    const inspector = screen.getByLabelText("Inspector");
    expect(factValue(inspector, "saved")).toBe("1");
    expect(factValue(inspector, "pending")).toBe("0");
    expect(factValue(inspector, "applied")).toBe("0");
  });

  it("re-reads for the imprint the operator switched TO", async () => {
    // The workspace (map + inspector + the edit store) is keyed by imprint,
    // so a switch remounts it. The visible half is asserted here; the half
    // that matters most -- one imprint's PENDING edits and etag never
    // reaching another's file -- has no UI gesture to produce it until S5,
    // and is why the `key` is there.
    const calls: string[] = [];
    render(<TestApp client={fakeApi(calls)} url="/design/corpapcn-golden" />);
    const inspector = await screen.findByLabelText("Inspector");
    await waitFor(() => expect(factValue(inspector, "saved")).toBe("1"));

    const outline = screen.getByLabelText("Imprints");
    fireEvent.click(within(outline).getByText("corpapcn-twin"));

    await waitFor(() =>
      expect(factValue(screen.getByLabelText("Inspector"), "saved")).toBe("0"),
    );
    expect(factValue(screen.getByLabelText("Inspector"), "etag")).toBe("\u2014");
    expect(calls).toContain("assertions:corpapcn-golden");
    expect(calls).toContain("assertions:corpapcn-twin");
  });
});

// ===========================================================================
// The router seam (tester pass).
//
// `model/zoom.ts` is round-trip tested in isolation, but the shell reads its
// input from React Router's `useLocation()`, not from a string a test handed
// it. These render through the REAL router so the second parser in the stack
// -- history's own encoding of a path -- is in the picture.
// ===========================================================================
describe("the URL through the real router", () => {
  it("carries a colon-bearing entity key into the zoom intact", async () => {
    // `encodeSegment` deliberately leaves `:` alone so the URL stays
    // readable. If history (or a future router version) re-encoded it, the
    // key would arrive as `address%3A10.1.10.222` and every lookup keyed on
    // it would miss -- silently, with a plausible-looking URL in the bar.
    render(
      <TestApp
        client={fakeApi()}
        url="/design/corpapcn-golden/hosts/address:10.1.10.222"
      />,
    );
    const inspector = await screen.findByLabelText("Inspector");
    expect(factValue(inspector, "showing")).toBe("one host");
    expect(factValue(inspector, "host")).toBe("address:10.1.10.222");
  });

  it("carries the nested host form's CIDR and MAC key too", async () => {
    render(
      <TestApp
        client={fakeApi()}
        url="/design/corpapcn-golden/segments/10.80.20.0-24/hosts/mac:aa:bb:cc:dd:ee:ff"
      />,
    );
    const inspector = await screen.findByLabelText("Inspector");
    expect(factValue(inspector, "host")).toBe("mac:aa:bb:cc:dd:ee:ff");
    expect(factValue(inspector, "segment")).toBe("10.80.20.0/24");
  });

  it("Escape walks up one level at a time and stops at the catalog", async () => {
    // `15` §7: Escape goes up, and it works because the level is in the URL.
    // The handler is a window listener the shell installs -- untested until
    // now, and the one piece of the zoom model that is NOT a pure function.
    render(
      <TestApp
        client={fakeApi()}
        url="/design/corpapcn-golden/segments/10.80.20.0-24/hosts/hostname:axdc01"
      />,
    );
    const inspector = await screen.findByLabelText("Inspector");
    expect(factValue(inspector, "showing")).toBe("one host");

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(factValue(screen.getByLabelText("Inspector"), "showing")).toBe("one segment"),
    );
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(factValue(screen.getByLabelText("Inspector"), "showing")).toBe("the whole network"),
    );
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(factValue(screen.getByLabelText("Inspector"), "showing")).toBe("the list of imprints"),
    );
    // At the root there is nowhere up: another Escape is a no-op, not a
    // crash and not a navigation to nothing.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(factValue(screen.getByLabelText("Inspector"), "showing")).toBe("the list of imprints");
  });

  it("keeps the filters while walking up (they describe the view, not the level)", async () => {
    render(
      <TestApp
        client={fakeApi()}
        url="/design/corpapcn-golden/segments/10.80.20.0-24?ghosts=1&class=imputed"
      />,
    );
    await screen.findByLabelText("Inspector");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(factValue(screen.getByLabelText("Inspector"), "showing")).toBe("the whole network"),
    );
    const inspector = screen.getByLabelText("Inspector");
    expect(factValue(inspector, "ghosts")).toBe("shown");
    expect(factValue(inspector, "class")).toBe("imputed");
  });

  it("renders `No such view` for a malformed escape rather than the catalog", async () => {
    // The parse returns `null` (unrecognized), never `{level:"catalog"}` --
    // and this is the path where that distinction is visible to an operator.
    render(<TestApp client={fakeApi()} url="/design/%zz" />);
    expect(await screen.findByText("No such view")).toBeDefined();
  });
});

describe("the provenance read has three outcomes, not one (M3)", () => {
  it("says LOADING while the read is in flight", async () => {
    let release: (value: ImprintProvenance) => void = () => {};
    const pending = new Promise<ImprintProvenance>((resolve) => {
      release = resolve;
    });
    render(
      <TestApp
        client={fakeApi([], { getProvenance: () => pending })}
        url="/design/corpapcn-golden?summary=1"
      />,
    );
    expect(await screen.findByTestId("evidence-loading")).toBeDefined();
    expect(screen.queryByTestId("evidence-absent")).toBeNull();
    expect(screen.queryByTestId("evidence-error")).toBeNull();

    release(PROVENANCE);
    expect(await screen.findByTestId("evidence-mix")).toBeDefined();
  });

  it("says the read FAILED, and does not blame the bundle", async () => {
    // The defect: `provenance == null` covered loading, failed AND absent, so
    // a 500 rendered as "no provenance sidecar on this bundle" -- a factual
    // claim about the artifact, made from a server error.
    const boom = new ConsoleApiError({
      kind: "unavailable",
      status: 503,
      message: "the bundle directory is not readable",
      url: "/api/imprints/corpapcn-golden/provenance",
    });
    render(
      <TestApp
        client={fakeApi([], { getProvenance: () => Promise.reject(boom) })}
        url="/design/corpapcn-golden"
      />,
    );
    // NOTE the URL: no `?summary=1`. A failed read is shown even while the
    // summary bar is COLLAPSED (R1) -- an error behind a disclosure is an
    // error nobody sees.
    const failure = await screen.findByTestId("evidence-error");
    expect(failure.textContent).toContain("unavailable");
    expect(failure.textContent).toContain("not readable");
    expect(failure.textContent).toMatch(/says nothing about whether the bundle/);
    expect(screen.queryByTestId("evidence-absent")).toBeNull();
    expect(screen.queryByTestId("evidence-mix")).toBeNull();
  });

  it("reports a failed provenance read even when the CAST read failed too (M1)", async () => {
    // The defect: `EvidenceMix` was mounted under `graph.cast &&` -- an
    // unrelated read -- so with the summary EXPANDED and no cast, a failed
    // provenance read rendered NOWHERE: the collapsed-bar branch was off
    // (expanded) and the expanded branch was gated on a cast that never came.
    const provBoom = new ConsoleApiError({
      kind: "unavailable",
      status: 503,
      message: "the sidecar is not readable",
      url: "/api/imprints/corpapcn-golden/provenance",
    });
    const castBoom = new ConsoleApiError({
      kind: "unavailable",
      status: 503,
      message: "the bundle directory is not readable",
      url: "/api/imprints/corpapcn-golden/cast",
    });
    render(
      <TestApp
        client={fakeApi([], {
          getProvenance: () => Promise.reject(provBoom),
          getCast: () => Promise.reject(castBoom),
        })}
        url="/design/corpapcn-golden?summary=1"
      />,
    );
    const failure = await screen.findByTestId("evidence-error");
    expect(failure.textContent).toContain("the sidecar is not readable");
    // ...and the cast's own failure is still stated separately.
    expect(screen.getByTestId("summary-bar").textContent).toContain(
      "the bundle directory is not readable",
    );
  });

  it("says ABSENT only for a successful read of a sidecar-less bundle", async () => {
    const absent: ImprintProvenance = {
      ...PROVENANCE,
      rows: [],
      sidecar_shape: "absent",
      sidecar_schema: null,
      code_fingerprint: null,
      population: {},
    };
    render(
      <TestApp
        client={fakeApi([], { getProvenance: () => Promise.resolve(absent) })}
        url="/design/corpapcn-twin?summary=1"
      />,
    );
    const none = await screen.findByTestId("evidence-absent");
    // Restated (Unified Console, plain words): still "absent, not empty" --
    // said as "not recorded, not none" -- and no `sidecar_shape` jargon.
    expect(none.textContent).toMatch(/No evidence record comes with this bundle/);
    expect(none.textContent).toMatch(/“not recorded”, not “none”/);
    expect(none.textContent).not.toMatch(/sidecar_shape/);
    expect(screen.queryByTestId("evidence-error")).toBeNull();
  });

  it("says NOT CONSUMED for a sidecar whose schema this Console does not read", async () => {
    // GAP-082 review: an unrecognized `schema` is PRESENT with zero rows
    // because nothing was consumed -- an empty legend would read "no evidence".
    const unrecognized: ImprintProvenance = {
      ...PROVENANCE,
      rows: [],
      sidecar_shape: "unrecognized",
      sidecar_schema: "axr-cast-provenance/2",
    };
    render(
      <TestApp
        client={fakeApi([], { getProvenance: () => Promise.resolve(unrecognized) })}
        url="/design/corpapcn-golden?summary=1"
      />,
    );
    const note = await screen.findByTestId("evidence-unrecognized");
    expect(note.textContent).toContain("axr-cast-provenance/2");
    expect(note.textContent).toMatch(/not consumed/);
    expect(screen.queryByTestId("evidence-mix")).toBeNull();
    expect(screen.queryByTestId("evidence-absent")).toBeNull();
  });
});

// ===========================================================================
// R1 -- the width slice (`docs/WORK_ORDER_console_level0_rendering.md` §3 R1).
// The summary strip and the outline pane both used to be permanent, and
// together they left the drawing a ~700 px column under a screenful of chrome
// (§2 F6). Both now collapse -- and each is tested for WHERE its state lives:
// the URL for the summary (so it survives a reload and travels in a link),
// `localStorage` for the outline (a per-viewer convenience that must NOT).
// ===========================================================================
describe("the summary bar and the outline toggle (R1)", () => {
  it("the summary is COLLAPSED by default: one digest line, no grid, no legend", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    const digest = await screen.findByTestId("summary-digest");
    expect(digest.textContent).toContain("32 segments");
    expect(digest.textContent).toContain("138 VMs");
    expect(digest.textContent).toContain("786 nodes / 1437 edges observed");
    expect(screen.queryByTestId("cast-numbers")).toBeNull();
    expect(screen.queryByTestId("evidence-mix")).toBeNull();
  });

  it("the toggle round-trips through the URL, not a store", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    const toggle = await screen.findByTestId("summary-toggle");
    // Collapsed: the control is a LINK to the same path plus `?summary=1`.
    expect(toggle.getAttribute("href")).toBe("/design/corpapcn-golden?summary=1");

    fireEvent.click(toggle);
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(screen.queryByTestId("summary-digest")).toBeNull();
    // Expanded: the same control now links back to the bare path.
    expect(screen.getByTestId("summary-toggle").getAttribute("href")).toBe(
      "/design/corpapcn-golden",
    );
  });

  it("`?summary=1` in the opening URL renders expanded (it survives a reload)", async () => {
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden?summary=1" />);
    expect(await screen.findByTestId("cast-numbers")).toBeDefined();
    expect(screen.queryByTestId("summary-digest")).toBeNull();
  });

  it("opening the summary does not disturb the filters it travels with", async () => {
    // One query carries both; the view flag must not perturb the filters it
    // shares the URL with. (The reverse -- `clear` not collapsing the bar --
    // is asserted against the real `FiltersBar` in `views.test.tsx`; this
    // file's fake rejects `getTopology`, so no filter bar renders here.)
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden?ghosts=1&summary=1" />);
    await screen.findByTestId("cast-numbers");
    const inspector = screen.getByLabelText("Inspector");
    expect(factValue(inspector, "ghosts")).toBe("shown");
    expect(factValue(inspector, "class")).toBe("any");
    expect(inspector.textContent).not.toMatch(/summary/i); // not a filter
  });

  it("the outline collapses, and the preference persists WITHOUT touching the URL", async () => {
    window.localStorage.clear();
    const first = render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    expect(await screen.findByLabelText("Imprints")).toBeDefined();

    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(screen.queryByLabelText("Imprints")).toBeNull();
    expect(window.localStorage.getItem("axrange.console.outline")).toBe("closed");
    first.unmount();

    // A fresh mount of the SAME url reads the preference back: it is a
    // per-viewer setting, so it survives a reload but is not in the link.
    render(<TestApp client={fakeApi()} url="/design/corpapcn-golden" />);
    await screen.findByTestId("summary-bar");
    expect(screen.queryByLabelText("Imprints")).toBeNull();
    expect(screen.getByTestId("outline-toggle").getAttribute("aria-expanded")).toBe("false");

    // Re-opening restores it, and leaves the store in the state it found it.
    fireEvent.click(screen.getByTestId("outline-toggle"));
    expect(await screen.findByLabelText("Imprints")).toBeDefined();
    window.localStorage.clear();
  });
});

