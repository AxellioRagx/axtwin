/**
 * R2 acceptance, verified INDEPENDENTLY of the tests that shipped with it
 * (`docs/WORK_ORDER_console_level0_rendering.md` §3 R2).
 *
 * The shipped R2 tests are real and cover most of the slice. This file fills
 * the two gaps they leave, both on paths an operator actually walks:
 *
 *  1. **The stamp's WRITE half.** `r2.trunk.test.ts` proves `pinPlaneStatus`
 *     classifies four states and `r2.ui.test.tsx` proves each one's note
 *     reaches the DOM; `tests/test_api_console.py` proves the server
 *     round-trips the token and never invents one. Nothing asserted the half
 *     that closes the loop — that the SPA *writes* the current stamp on the
 *     next save, which is the whole of "legacy is accepted, flagged, and
 *     stamped on next write". Asserted here on the actual `putLayout` body,
 *     through the real `ConsoleShell`, not on a prop.
 *
 *  2. **A guard on the crossing guard.** `r2.trunk.test.ts` asserts that a pin
 *     which drops a site on another group's lanes produces "no *trunk*
 *     violation, every violation is a lane". Both halves of that pass
 *     VACUOUSLY on an empty array — `[].every(...)` is `true` — so if that pin
 *     happened to cause no crossing at all, the assertion would prove nothing
 *     about the trunk. This pins the premise.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConsoleApi } from "../src/api/client";
import type {
  AssertionsDoc,
  CastSummary,
  ImprintList,
  ImprintProvenance,
  LayoutDoc,
} from "../src/api/types";
import { TestApp } from "../src/routes";
import { buildGraph } from "../src/model/graph";
import { layout0 } from "../src/layout/level0";
import { PLANE_STAMP } from "../src/layout/plane";
import { pinPlaneNote } from "../src/views/Level0";
import type { Pins } from "../src/layout/pins";
import { noConnectorCrossesATile } from "./layout/helpers";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const IMPRINT = "trimmed-fixture";
const graph = buildGraph(trimmedExport, trimmedCast);

// ---------------------------------------------------------------------------
// A fake API that serves enough for the real shell to draw level 0, with the
// layout document under the test's control.
// ---------------------------------------------------------------------------
const CAST: CastSummary = {
  id: IMPRINT,
  bundle_dir: `/b/${IMPRINT}`,
  twin_config_id: IMPRINT,
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
  has_layout: true,
  provenance_schema: null,
  provenance_path: null,
  sidecar_shape: "absent",
  evidence_source: "",
  warnings: [],
  conduit_roster: [...(trimmedCast.conduits ?? [])],
  routers: (trimmedCast.routers ?? []).map((r) => ({ name: r.name, segments: [...r.segments] })),
};

const PROV_ABSENT: ImprintProvenance = {
  imprint_id: IMPRINT,
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
      id: IMPRINT,
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
  imprint_id: IMPRINT,
  path: `/b/${IMPRINT}/${IMPRINT}.assertions.yaml`,
  exists: false,
  etag: null,
  schema: "axr-assertions/1",
  imprint: IMPRINT,
  as_of: null,
  source: null,
  add: [],
  override: [],
  suppress: [],
  warnings: [],
};

/** Re-aimed for R6's plane: the per-group budget narrowed a small group's
 *  column from three tiles to two, so the whole plane narrowed and x=900 now
 *  parks this column over `site:misc`'s tiles. x=420 is measured clean (the
 *  sweep is quoted in `r2.trunk.test.ts`). The plane-stamp tests below do not
 *  care where the pin is; the two crossing tests do. */
const SAVED_PINS: Pins = { "site:10.80": { x: 420, y: 620 } };
/** Two pins on file, so "one re-placed" and "all re-placed" are different
 *  states -- the whole point of M1. */
const TWO_PINS: Pins = {
  "site:10.80": { x: 420, y: 620 },
  "site:10.20": { x: 300, y: 620 },
};

function layoutDoc(plane: string | null, pins: Pins = SAVED_PINS): LayoutDoc {
  return {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/${IMPRINT}.layout.json`,
    exists: true,
    etag: "etag-1",
    schema: "axr-layout/1",
    imprint: IMPRINT,
    pins: { ...pins },
    plane,
  };
}

/** The captured `putLayout` calls, so a test can read the document the SPA
 *  actually sent rather than a prop it was handed. */
function harness(
  plane: string | null,
  startingPins: Pins = SAVED_PINS,
): {
  client: ConsoleApi;
  puts: Array<{ id: string; doc: Record<string, unknown>; etag: string | null }>;
} {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const puts: Array<{ id: string; doc: Record<string, unknown>; etag: string | null }> = [];
  const client: ConsoleApi = {
    listImprints: () => Promise.resolve(CATALOG),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROV_ABSENT),
    getTopology: () => Promise.resolve(trimmedExport),
    getCast: () => Promise.resolve(CAST),
    getAssertions: () => Promise.resolve(EMPTY_ASSERTIONS),
    putAssertions: unused("putAssertions"),
    getLayout: () => Promise.resolve(layoutDoc(plane, startingPins)),
    putLayout: vi.fn((id: string, doc: Record<string, unknown>, etag: string | null) => {
      puts.push({ id, doc, etag });
      return Promise.resolve({
        ...layoutDoc((doc["plane"] as string | null) ?? null, (doc["pins"] ?? {}) as Pins),
        etag: "etag-2",
      });
    }),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
  return { client, puts };
}

// ===========================================================================
// 1. The stamp is WRITTEN on the next save -- the half that closes the loop.
// ===========================================================================
describe("R2: a saved layout is stamped with the plane it was drawn on", () => {
  it("a LEGACY document is flagged, and the next save stamps it", async () => {
    const { client, puts } = harness(null);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");

    // Flagged, and the pins are still applied (never silently dropped).
    const note = await screen.findByTestId("pins-plane-note");
    expect(note.textContent).toContain("before the Console recorded which layout");

    // `Reset pins` is the shortest real path through the SPA's own save.
    fireEvent.click(screen.getByTestId("pin-reset"));
    await waitFor(() => expect(puts).toHaveLength(1));

    // THE POINT: the document that went to the server carries THIS build's
    // plane. Without it the file stays unstamped for ever and every future
    // read re-reports it as legacy.
    expect(puts[0]!.doc["plane"]).toBe(PLANE_STAMP);
    expect(puts[0]!.doc["imprint"]).toBe(IMPRINT);
    expect(puts[0]!.etag).toBe("etag-1"); // the read's etag, not invented
  });

  it("a STALE document is re-stamped to the CURRENT plane by a RESET (the whole set re-authored)", async () => {
    const stale = "l0-1-00000000";
    const { client, puts } = harness(stale);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");

    const note = await screen.findByTestId("pins-plane-note");
    expect(note.textContent).toContain(stale);
    expect(note.textContent).toContain(PLANE_STAMP);

    fireEvent.click(screen.getByTestId("pin-reset"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.doc["plane"]).toBe(PLANE_STAMP);
    expect(puts[0]!.doc["plane"]).not.toBe(stale);
  });

  it("a CURRENT document says nothing, and still writes the stamp on save", async () => {
    const { client, puts } = harness(PLANE_STAMP);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    expect(screen.queryByTestId("pins-plane-note")).toBeNull();

    fireEvent.click(screen.getByTestId("pin-reset"));
    await waitFor(() => expect(puts).toHaveLength(1));
    // A save must not DROP the stamp on the way back out, which would demote
    // a current file to legacy on the next read.
    expect(puts[0]!.doc["plane"]).toBe(PLANE_STAMP);
  });
});

// ===========================================================================
// 1b. M1 ACROSS THE SEAM: does the writer's rule reach the wire?
//
// The shipped M1 tests cover the two ends and not the join: `planeForSave` is
// exercised directly (`r2.trunk.test.ts`) and `usePinDrag`'s re-authored set is
// read off an `onCommit` spy (`r2.ui.test.tsx`). Neither crosses
// `usePinDrag → Level0 → MapPane → App.savePins → planeForSave → PUT`. If that
// second argument were dropped anywhere along it, `planeForSave` would take the
// conservative branch and BOTH shipped tests would still pass while a fully
// re-placed set never cleared its warning; if a wider set were passed, stale
// pins would launder -- the original M1 bug -- and again neither would notice.
// These drive a REAL drag through the mounted shell and read the PUT body.
// ===========================================================================
const STALE = "l0-1-00000000";

/** A real pointer drag on a pinnable group, through the mounted SVG. jsdom
 *  offers no CTM and a zero-sized rect, so `layoutScaleOf` falls back to
 *  identity -- a client delta IS a layout delta, and nothing needs stubbing. */
function dragSite(key: string, to: { x: number; y: number }): void {
  const svg = screen.getByTestId("level0-svg");
  const handle = svg.querySelector(`[data-pinnable="${key}"]`);
  expect(handle, `no pinnable handle for ${key}`).not.toBeNull();
  fireEvent.pointerDown(handle!, { button: 0, pointerId: 1, clientX: 0, clientY: 0 });
  fireEvent.pointerMove(svg, { pointerId: 1, clientX: to.x, clientY: to.y });
  fireEvent.pointerUp(svg, { pointerId: 1, clientX: to.x, clientY: to.y });
}

describe("M1 end to end: re-placing ONE pin does not launder the rest", () => {
  it("a STALE document stays stale: the PUT keeps the old stamp, the note stays", async () => {
    const { client, puts } = harness(STALE, TWO_PINS);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    expect((await screen.findByTestId("pins-plane-note")).textContent).toContain(STALE);

    dragSite("site:10.80", { x: 60, y: 45 });
    await waitFor(() => expect(puts).toHaveLength(1));

    // THE POINT: one drag re-places one pin, so the document keeps the stamp
    // it arrived with -- the untouched `site:10.20` is never relabelled as
    // authored against the plane now on screen.
    expect(puts[0]!.doc["plane"]).toBe(STALE);
    expect(puts[0]!.doc["plane"]).not.toBe(PLANE_STAMP);
    // Both pins are written (a commit is the whole set -- that IS the bug's
    // shape), and the one nobody touched keeps its saved coordinates.
    const written = puts[0]!.doc["pins"] as Pins;
    expect(Object.keys(written).sort()).toEqual(["site:10.20", "site:10.80"]);
    expect(written["site:10.20"]).toEqual(TWO_PINS["site:10.20"]);

    // ...and the warning is still on screen afterwards: the reader and the
    // writer agree, which is what stops the note retiring itself.
    expect(screen.getByTestId("pins-plane-note").textContent).toContain(STALE);
  });

  it("a LEGACY document stays legacy on a partial re-place", async () => {
    const { client, puts } = harness(null, TWO_PINS);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    await screen.findByTestId("pins-plane-note");

    dragSite("site:10.80", { x: 60, y: 45 });
    await waitFor(() => expect(puts).toHaveLength(1));
    // `null` in, `null` out: an unstamped file is not stamped by a partial
    // re-place, and no value is invented for it.
    expect(puts[0]!.doc["plane"]).toBeNull();
    expect(screen.getByTestId("pins-plane-note")).toBeDefined();
  });

  it("re-placing EVERY surviving pin advances the stamp, and clears the note", async () => {
    const { client, puts } = harness(STALE, TWO_PINS);
    render(<TestApp client={client} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");

    dragSite("site:10.80", { x: 60, y: 45 });
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.doc["plane"]).toBe(STALE); // still partial

    dragSite("site:10.20", { x: 70, y: 55 });
    await waitFor(() => expect(puts).toHaveLength(2));
    // The set now describes THIS plane, so the stamp advances -- which is the
    // on-screen advice ("re-place every one") actually working.
    expect(puts[1]!.doc["plane"]).toBe(PLANE_STAMP);
    await waitFor(() => expect(screen.queryByTestId("pins-plane-note")).toBeNull());
  });

  it("the advice on screen matches the rule the code applies", () => {
    // A note telling the operator to do something that does not work is worse
    // than no note. Both wordings must name the WHOLE-set action.
    for (const status of ["legacy", "stale"] as const) {
      const note = pinPlaneNote(status, STALE) ?? "";
      expect(note, status).toMatch(/re-place every one/);
      expect(note, status).toMatch(/reset/);
    }
  });
});

// ===========================================================================
// 2. The guard on the crossing guard.
// ===========================================================================
describe("R2: the pinned-site crossing assertion, after R4 tightened it", () => {
  // **Restated, not deleted (LES-299).** The original test here pinned the
  // PREMISE of `r2.trunk.test.ts`'s "every violation is a lane": that the pin
  // produced at least one violation, since `[].every(...)` is vacuously true.
  // R4 retired the lanes, and this pin now produces NONE -- so the premise is
  // gone, the trunk test tightened to an exact `[]`, and what still needs
  // guarding is the opposite: that the check would SEE a violation if there
  // were one. Same job, other end.
  it("produces no violation at all now -- which is why `[]` is assertable", () => {
    expect(noConnectorCrossesATile(layout0(graph, SAVED_PINS))).toEqual([]);
  });

  it("and the check is not simply blind: a connector through a tile IS named", () => {
    const l0 = layout0(graph, SAVED_PINS);
    const tile = l0.tiles[0]!;
    const cx = tile.box.x + tile.box.w / 2;
    const probe = {
      ...l0,
      infraStubs: [
        {
          deviceId: "probe",
          siteKey: tile.siteKey,
          points: [
            { x: cx, y: tile.box.y - 40 },
            { x: cx, y: tile.box.y + tile.box.h + 40 },
          ],
        },
      ],
    };
    expect(noConnectorCrossesATile(probe)).toEqual([
      `infra stub probe crosses tile ${tile.segment.observedCidr}`,
    ]);
  });

  it("and the unpinned layout crosses nothing either", () => {
    expect(noConnectorCrossesATile(layout0(graph))).toEqual([]);
  });
});
