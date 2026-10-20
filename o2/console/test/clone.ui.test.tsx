/**
 * S5.1.b (clone and drag) and S5.1.c (generated hosts) through the REAL shell
 * (`TestApp` -> `ConsoleShell` -> `ImprintWorkspace` -> `MapPane`/`Level0` and
 * `Inspector`/`ElementActions`) against a STATEFUL fake API -- a PUT writes the
 * file a later GET reads, for the assertions file and the layout alike.
 *
 *   - acceptance 5: a keyboard-only operator completes a clone through the
 *     inspector's button and the placement controls;
 *   - touch: a tap on the map places the proposed clone;
 *   - the Alt-drag: a ghost follows the pointer and the source does not move;
 *   - acceptance 6: the pending clone tile renders HATCHED at its drop position,
 *     is selectable, and a drop into a COLLAPSED group is held, disclosed, and
 *     drawn on `?expand=`;
 *   - the clone's pin goes through the S4 writer and keeps LES-296's stamp rule;
 *   - acceptance 4 in the UI: a refusal is said and stages nothing;
 *   - acceptance 7: the site `+` generates hosts at .10/.11/.12, skipping taken
 *     addresses, with the role's persona listen set -- or an empty one, said.
 *
 * The pure rules are `clone.test.ts`; the written file is `batch.roundtrip.test.ts`.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type {
  AssertionsDoc,
  CastSummary,
  ImprintList,
  ImprintProvenance,
  LayoutDoc,
  TopologyExport,
} from "../src/api/types";
import { proposedClonePlacement } from "../src/layout/added";
import { TILE_GAP_Y, TILE_H, TILE_W } from "../src/layout/geometry";
import { layout0 } from "../src/layout/level0";
import { PLANE_STAMP } from "../src/layout/plane";
import type { Pins } from "../src/layout/pins";
import { buildGraph } from "../src/model/graph";
import { ROLE_LISTEN_DEFAULTS } from "../src/model/roleListen.generated";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { castInputFrom } from "../src/views/useGraph";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const IMPRINT = "trimmed-fixture";
const SOURCE = "10.80.20.0/24";
const NOTE = "a second lab floor";

function castFor(exportData: TopologyExport): CastSummary {
  return {
    id: IMPRINT,
    bundle_dir: `/b/${IMPRINT}`,
    twin_config_id: IMPRINT,
    description: null,
    pit_id: "pit-1",
    bundle_schema: "axr-topology/1.5",
    topology_schema_version: "1.3",
    code_fingerprint: null,
    params: {},
    deploy_min_occupancy: trimmedCast.deployMinOccupancy,
    deploy_min_occupancy_source: "declared",
    segments: exportData.segments.length,
    node_specs: 40,
    vms: 40,
    hosts: 40,
    conduits: (trimmedCast.conduits ?? []).length,
    // The live shape: the conduit roster AND the segment roster are published.
    conduit_roster: [...(trimmedCast.conduits ?? [])],
    routers: [],
    segment_roster: exportData.segments.map((s) => ({
      name: s.name,
      cidr: s.cidr,
      zone_type: trimmedCast.zoneTypeByName?.[s.name] ?? null,
    })),
    observed_nodes: exportData.nodes.length,
    observed_edges: exportData.edges.length,
    observed_segments: exportData.segments.length,
    census: {},
    has_provenance: false,
    has_topology_export: true,
    has_assertions: true,
    has_layout: false,
    provenance_schema: null,
    provenance_path: null,
    sidecar_shape: "absent",
    evidence_source: "none",
    warnings: [],
  };
}

const PROVENANCE: ImprintProvenance = {
  imprint_id: IMPRINT,
  rows: [],
  sidecar_shape: "absent",
  sidecar_schema: null,
  evidence_source: "none",
  code_fingerprint: null,
  population: {},
  warnings: [],
};

function fileDoc(overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/${IMPRINT}.assertions.yaml`,
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: IMPRINT,
    as_of: "2026-09-15T00:00:00Z",
    source: "operator: tester",
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

interface Server {
  api: ConsoleApi;
  assertionPuts: Array<Record<string, unknown>>;
  layoutPuts: Array<{ doc: Record<string, unknown>; etag: string | null }>;
}

/** A fake with a FILE and a LAYOUT: each PUT replaces what a later GET reads. */
function server(opts: {
  exportData?: TopologyExport;
  file?: AssertionsDoc;
  pins?: Pins;
  plane?: string | null;
  /** Make every `PUT .../layout` fail with this error (review S3). */
  layoutPutFails?: ConsoleApiError;
} = {}): Server {
  const exportData = opts.exportData ?? trimmedExport;
  let file = opts.file ?? fileDoc();
  let layout: LayoutDoc = {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/layout.json`,
    exists: opts.pins !== undefined,
    etag: "layout-1",
    schema: "axr-layout/1",
    imprint: IMPRINT,
    pins: { ...(opts.pins ?? {}) },
    plane: opts.plane ?? null,
  };
  let writes = 1;
  const assertionPuts: Server["assertionPuts"] = [];
  const layoutPuts: Server["layoutPuts"] = [];
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const api: ConsoleApi = {
    listImprints: () =>
      Promise.resolve({
        imprints: [{ id: IMPRINT, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" }],
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROVENANCE),
    getTopology: () => Promise.resolve(exportData),
    getCast: () => Promise.resolve(castFor(exportData)),
    getAssertions: () => Promise.resolve(file),
    putAssertions: (_id, document) => {
      assertionPuts.push(document);
      writes += 1;
      file = fileDoc({
        etag: `etag-${writes}`,
        add: (document["add"] as AssertionsDoc["add"]) ?? [],
        override: (document["override"] as AssertionsDoc["override"]) ?? [],
        suppress: (document["suppress"] as AssertionsDoc["suppress"]) ?? [],
      });
      return Promise.resolve(file);
    },
    getLayout: () => Promise.resolve(layout),
    putLayout: (_id, doc, etag) => {
      layoutPuts.push({ doc, etag });
      if (opts.layoutPutFails) return Promise.reject(opts.layoutPutFails);
      writes += 1;
      layout = {
        ...layout,
        exists: true,
        etag: `layout-${writes}`,
        pins: { ...((doc["pins"] ?? {}) as Pins) },
        plane: (doc["plane"] as string | null | undefined) ?? null,
      };
      return Promise.resolve(layout);
    },
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
  return { api, assertionPuts, layoutPuts };
}

function LocationProbe(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="loc">{`${loc.pathname}${loc.search}`}</output>;
}

function open(url: string, api: ConsoleApi): ReturnType<typeof render> {
  return render(
    <TestApp client={api} url={url}>
      <>
        <ConsoleRoutes />
        <LocationProbe />
      </>
    </TestApp>,
  );
}

const loc = (): string => screen.getByTestId("loc").textContent ?? "";
const selectionKind = (): string | null =>
  screen.queryByTestId("element-actions")?.getAttribute("data-selection") ?? null;
const title = (): string | null => screen.queryByTestId("selection-title")?.textContent ?? null;

function tilePencil(container: HTMLElement, cidr: string): HTMLElement {
  const rect = container.querySelector(`[data-testid="tile-actions"][data-cidr="${cidr}"]`);
  if (!rect?.parentElement) throw new Error(`no tile ✎ for ${cidr}`);
  return rect.parentElement;
}

function addedTile(container: HTMLElement, cidr: string): Element | null {
  return container.querySelector(`[data-testid="added-tile"][data-cidr="${cidr}"]`);
}

/**
 * Expand every gesture group in the change list, so a test can count the
 * RECORDS again.
 *
 * RESTATED for S5.1.d (LES-299): a batch is now ONE collapsed entry carrying
 * its counts (`change-gesture-row`), not N rows. The records are still there
 * and still individually removable -- they are one click away. Every count
 * below that used to read `change-row` therefore expands first, and the
 * headline's own counts are asserted where the gesture is the subject.
 */
function expandGestures(): void {
  for (const button of screen.queryAllByTestId("gesture-expand")) fireEvent.click(button);
}

/** The pure layout the map draws for the fixture with no pins -- through the
 *  same seam, so a coordinate is DERIVED, never a plane-specific literal
 *  (LES-317). */
function drawnLayout(exportData: TopologyExport = trimmedExport) {
  return layout0(buildGraph(exportData, castInputFrom(castFor(exportData), IMPRINT)));
}

/** Select the source tile and press *Clone this segment…* -- keyboard only. */
async function startCloneByKeyboard(container: HTMLElement): Promise<void> {
  await screen.findByTestId("level0-svg");
  const pencil = tilePencil(container, SOURCE);
  pencil.focus();
  fireEvent.keyDown(pencil, { key: "Enter" });
  await waitFor(() => expect(selectionKind()).toBe("segment"));
  const clone = screen.getByTestId("action-open-clone");
  expect(clone.tagName).toBe("BUTTON");
  clone.focus();
  // A native button: Enter and Space activate it by dispatching `click`, with no
  // pointer involved -- jsdom does not synthesize that, so the click IS the key.
  fireEvent.click(clone);
  await waitFor(() => expect(selectionKind()).toBe("clone"));
}

// ===========================================================================
// acceptance 5 -- keyboard only
// ===========================================================================
describe("S5.1.b acceptance 5 -- a keyboard-only operator completes a clone through the inspector button and placement", () => {
  it("Enter on the tile's ✎, Clone this segment…, a nudge, Place here, a note, Stage -- no pointer anywhere", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);

    // The clone is a NEW select gesture: focus moved into its frame.
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
    expect(title()).toBe(`Clone ${SOURCE}`);
    // The map shows the dashed proposal and says how to place it.
    expect(screen.getByTestId("placement-bar").textContent).toMatch(/click or tap the map/);
    const proposed = proposedClonePlacement(drawnLayout(), SOURCE);
    const draft = screen.getByTestId("clone-draft-tile");
    expect(draft.getAttribute("data-placed")).toBe("false");
    expect(draft.querySelector("rect")!.getAttribute("class")).toBe("added-tile is-draft");
    expect(draft.querySelector("rect")!.getAttribute("x")).toBe(String(proposed.x));

    // Every placement control is a native button.
    for (const id of ["clone-nudge-left", "clone-nudge-up", "clone-nudge-down", "clone-nudge-right", "clone-place", "clone-cancel"]) {
      expect(screen.getByTestId(id).tagName, id).toBe("BUTTON");
    }
    fireEvent.click(screen.getByTestId("clone-nudge-down"));
    const at = { x: proposed.x, y: proposed.y + TILE_H + TILE_GAP_Y };
    expect(screen.getByTestId("clone-placement-at").textContent).toBe(`proposed at x ${at.x}, y ${at.y}`);

    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    // Placing swapped the placement controls away (the form is mounted
    // throughout since review S1) and focus went INTO the form -- not to `body`
    // when the pressed button unmounted (LES-324).
    expect(screen.queryByTestId("clone-placement")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("clone-placed"));
    expect((within(form).getByTestId("clone-cidr") as HTMLInputElement).value).toBe("10.80.0.0/24");
    expect((within(form).getByTestId("clone-name") as HTMLInputElement).value).toBe("seg-10-80-0-0");
    expect(within(form).getByTestId("clone-summary").textContent).toBe("stages 1 segment · 17 hosts · 0 conduits");
    expect(within(form).getByTestId("clone-not-cloned").textContent).toMatch(/not cloned: 1 host.*hostname:axdc01/);

    const note = within(form).getByTestId("gesture-note");
    note.focus();
    fireEvent.change(note, { target: { value: NOTE } });
    fireEvent.submit(form); // Enter in a form field submits it

    // One batch, now ONE change-list entry (S5.1.d) carrying the work order's
    // own shape, expanding to the segment and its 17 address-keyed hosts.
    const gestureRow = await screen.findByTestId("change-gesture-row");
    expect(within(gestureRow).getByTestId("change-gesture").textContent).toBe(`clone of ${SOURCE} → 10.80.0.0/24`);
    expect(within(gestureRow).getByTestId("gesture-counts").textContent).toBe("1 segment · 17 hosts");
    expect(within(gestureRow).getByTestId("change-state").textContent).toBe("pending (browser only)");
    expect(screen.queryAllByTestId("change-row")).toHaveLength(0);
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(18);
    // The drop position IS an S4 pin on the new key.
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    expect((srv.layoutPuts[0]!.doc["pins"] as Pins)["segment:10.80.0.0/24"]).toEqual(at);
    // The clone is selected: the added segment's actions, one step away.
    await waitFor(() => expect(title()).toBe("Segment 10.80.0.0/24"));
    expect(screen.getByTestId("added-segment-facts").textContent).toContain(`cloned from${SOURCE}`);
    // ...and drawn, hatched, where it was placed.
    const tile = addedTile(container, "10.80.0.0/24")!;
    expect(tile.getAttribute("x")).toBe(String(at.x));
    expect(tile.getAttribute("y")).toBe(String(at.y));
    expect(tile.getAttribute("fill")).toMatch(/^url\(#pending-hatch-/);
    // Nothing is saved to the assertions file by staging.
    expect(srv.assertionPuts).toHaveLength(0);
  });

  it("Escape cancels a clone being placed: the proposal and the bar are gone, nothing is staged or pinned", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    expect(screen.getByTestId("clone-draft-tile")).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(selectionKind()).toBeNull());
    expect(screen.queryByTestId("clone-draft-tile")).toBeNull();
    expect(screen.queryByTestId("placement-bar")).toBeNull();
    expect(screen.queryByTestId("change-row")).toBeNull();
    expect(srv.layoutPuts).toHaveLength(0);
  });
});

// ===========================================================================
// touch -- a tap on the map places the clone
// ===========================================================================
describe("S5.1.b -- by touch: a tap on the map places the proposed clone where it lands", () => {
  it("centres the clone on the tap, opens the form, and does not drill into the tile under the finger", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    const before = loc();

    // A tap is a click. jsdom lays nothing out, so the map's client->layout
    // mapping is 1:1 from the viewBox origin (0, 0 with no pins).
    const tap = { x: 700, y: 520 };
    fireEvent.click(screen.getByTestId("placement-overlay"), { clientX: tap.x, clientY: tap.y });
    const form = await screen.findByTestId("form-clone");
    const at = { x: Math.round(tap.x - TILE_W / 2), y: Math.round(tap.y - TILE_H / 2) };
    expect(within(form).getByTestId("clone-placed").textContent).toMatch(new RegExp(`^placed at x ${at.x}, y ${at.y} `));
    const draft = screen.getByTestId("clone-draft-tile");
    expect(draft.getAttribute("data-placed")).toBe("true");
    expect(draft.querySelector("rect")!.getAttribute("x")).toBe(String(at.x));
    // The overlay is gone once placed, and the route did not change.
    expect(screen.queryByTestId("placement-overlay")).toBeNull();
    expect(loc()).toBe(before);

    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    expect((srv.layoutPuts[0]!.doc["pins"] as Pins)["segment:10.80.0.0/24"]).toEqual(at);
  });
});

// ===========================================================================
// Alt-drag
// ===========================================================================
describe("S5.1.b -- an Alt-drag of a tile drops a clone where the pointer is released", () => {
  it("drags a ghost, leaves the source where it is, opens the form at the drop, and pins nothing until Stage", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    const svg = await screen.findByTestId("level0-svg");
    const handle = svg.querySelector(`[data-pinnable="segment:${SOURCE}"]`)!;
    const sourceRect = handle.querySelector('[data-testid="segment-tile"]')!;
    const origin = { x: Number(sourceRect.getAttribute("x")), y: Number(sourceRect.getAttribute("y")) };
    const before = loc();

    fireEvent.pointerDown(handle, { button: 0, pointerId: 7, clientX: 0, clientY: 0, altKey: true });
    fireEvent.pointerMove(svg, { pointerId: 7, clientX: 420, clientY: 260 });
    const ghost = screen.getByTestId("clone-ghost");
    expect(ghost.getAttribute("x")).toBe(String(origin.x + 420));
    // The SOURCE did not move -- an Alt-drag is a clone, not a pin.
    expect(sourceRect.getAttribute("x")).toBe(String(origin.x));
    fireEvent.pointerUp(svg, { pointerId: 7, clientX: 420, clientY: 260 });
    // The drag's own trailing click must not drill into the source.
    fireEvent.click(handle);
    expect(loc()).toBe(before);

    await waitFor(() => expect(selectionKind()).toBe("clone"));
    const drop = { x: origin.x + 420, y: origin.y + 260 };
    const form = await screen.findByTestId("form-clone");
    expect(within(form).getByTestId("clone-placed").textContent).toMatch(new RegExp(`^placed at x ${drop.x}, y ${drop.y} `));
    expect(screen.queryByTestId("clone-ghost")).toBeNull();
    expect(srv.layoutPuts).toHaveLength(0);
    // A drag WITHOUT Alt is still S4's pin (the source moves), proven elsewhere;
    // here, the tile is untouched and nothing was committed by the drag itself.
    expect(sourceRect.getAttribute("x")).toBe(String(origin.x));
  });
});

// ===========================================================================
// acceptance 6 -- hatched at the drop position; held in a collapsed group
// ===========================================================================
describe("S5.1.b acceptance 6 -- the pending clone tile", () => {
  async function cloneAt(srv: Server, drag: { x: number; y: number }): Promise<{ x: number; y: number }> {
    const svg = await screen.findByTestId("level0-svg");
    const handle = svg.querySelector(`[data-pinnable="segment:${SOURCE}"]`)!;
    const rect = handle.querySelector('[data-testid="segment-tile"]')!;
    const origin = { x: Number(rect.getAttribute("x")), y: Number(rect.getAttribute("y")) };
    fireEvent.pointerDown(handle, { button: 0, pointerId: 3, clientX: 0, clientY: 0, altKey: true });
    fireEvent.pointerMove(svg, { pointerId: 3, clientX: drag.x, clientY: drag.y });
    fireEvent.pointerUp(svg, { pointerId: 3, clientX: drag.x, clientY: drag.y });
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    await waitFor(() => expect(srv.layoutPuts.length).toBeGreaterThan(0));
    return { x: origin.x + drag.x, y: origin.y + drag.y };
  }

  it("renders HATCHED at its drop position, says it is a pending clone, and selecting it offers the added segment's actions", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    const drop = await cloneAt(srv, { x: 900, y: 40 });

    const tile = addedTile(container, "10.80.0.0/24")!;
    expect(tile).not.toBeNull();
    expect(tile.getAttribute("data-state")).toBe("pending");
    expect(tile.getAttribute("x")).toBe(String(drop.x));
    expect(tile.getAttribute("y")).toBe(String(drop.y));
    // Hatched: the fill is the map's own pending pattern, and the pattern exists.
    // The class list is PINNED because a stylesheet `fill` would override the
    // attribute in a browser -- `placement.test.ts` proves no rule in the
    // theme sets the fill of exactly these classes (jsdom applies no CSS).
    expect(tile.getAttribute("class")).toBe("added-tile is-pending");
    const fill = tile.getAttribute("fill")!;
    const id = /^url\(#(.+)\)$/.exec(fill)![1]!;
    expect(container.querySelector(`pattern[id="${id}"]`)).not.toBeNull();
    // Painted AFTER the east-west band, so no arc crosses the operator's own
    // pending tile (a clone is often dropped in the band).
    const band = screen.getByTestId("east-west-band");
    expect(band.compareDocumentPosition(tile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Its accessible name says what it is, in words.
    const group = tile.parentElement!;
    expect(group.getAttribute("aria-label")).toBe(
      `Pending clone of ${SOURCE}: segment 10.80.0.0/24 (seg-10-80-0-0), 17 hosts staged — select it for its actions`,
    );

    // Move the selection away, then select the tile: the added segment's frame.
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(selectionKind()).toBeNull());
    fireEvent.click(group);
    await waitFor(() => expect(title()).toBe("Segment 10.80.0.0/24"));
    expect(group.getAttribute("aria-current")).toBe("true");
    expect(screen.getByTestId("added-segment-facts").textContent).toContain("staged hosts17");
    expect(screen.getByTestId("added-segment-note").textContent).toMatch(/^drawn on the network map at its placement, hatched while it is pending/);
    // An added segment cannot be cloned in turn: said at the control.
    expect(screen.getByTestId("action-open-clone").getAttribute("aria-disabled")).toBe("true");
  });

  it("a drop into a COLLAPSED group is held -- not drawn, said in the budget note -- and drawn on ?expand=", async () => {
    const over = withOverBudgetSite();
    const srv = server({ exportData: over });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("level0-svg");
    // Start by keyboard (the tile is in the collapsed group's shown six), place.
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    // The form says the clone is held, before Stage, with the way to see it.
    expect(within(form).getByTestId("clone-held").textContent).toMatch(/^its group 10\.80 is collapsed on the map, so the clone is held there/);
    // ...and so does the map, for the clone being filled in.
    expect(screen.getByTestId("budget-note").textContent).toContain("the clone being filled in held in 10.80");
    expect(screen.queryByTestId("clone-draft-tile")).toBeNull();

    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    const pin = (srv.layoutPuts[0]!.doc["pins"] as Pins)["segment:10.80.0.0/24"]!;

    // HELD: the pin is kept, the tile is not drawn, and the note says so.
    await waitFor(() => expect(title()).toBe("Segment 10.80.0.0/24"));
    expect(addedTile(container, "10.80.0.0/24")).toBeNull();
    const note = screen.getByTestId("budget-note").textContent ?? "";
    expect(note).toContain(" · 1 pending clone held in 10.80 — not drawn while its group is collapsed: open it to see it");
    expect(screen.getByTestId("level0-desc").textContent).toContain("1 pending clone held in 10.80");

    // OPEN THE GROUP: the URL's query changes, the selection stays, the tile is drawn at its pin.
    fireEvent.click(screen.getByTestId("overflow-tile").parentElement!);
    await waitFor(() => expect(loc()).toContain("expand=site:10.80"));
    await waitFor(() => expect(addedTile(container, "10.80.0.0/24")).not.toBeNull());
    const tile = addedTile(container, "10.80.0.0/24")!;
    expect(tile.getAttribute("x")).toBe(String(pin.x));
    expect(tile.getAttribute("y")).toBe(String(pin.y));
    expect(screen.getByTestId("budget-note").textContent).not.toContain("held in");
    expect(title()).toBe("Segment 10.80.0.0/24");
  });
});

// ===========================================================================
// taking a clone back -- what exists before S5.1.d's whole-gesture undo
// ===========================================================================
describe("S5.1.b -- removing a clone's SEGMENT row takes the records it is the only home of, after listing them", () => {
  it("lists the 17 hosts and 2 mirrored conduits, and 'remove all' leaves the change list empty", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.click(within(form).getByTestId("clone-mirror"));
    expect(within(form).getByTestId("clone-summary").textContent).toBe("stages 1 segment · 17 hosts · 2 conduits");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    const row = await screen.findByTestId("change-gesture-row");
    expect(within(row).getByTestId("gesture-counts").textContent).toBe("1 segment · 17 hosts · 2 conduits");
    expandGestures();
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(20));

    // The segment's own row, from inside the expanded gesture: S5.1.a's
    // only-home rule is unchanged, and still reachable per record (S5.1.d
    // keeps single removal beside the whole-gesture undo).
    const segmentRow = screen.getAllByTestId("change-row").find((r) => r.textContent?.includes("+ segment 10.80.0.0/24"))!;
    fireEvent.click(within(segmentRow).getByTestId("change-remove"));
    const confirm = screen.getByTestId("remove-confirm");
    expect(within(confirm).getAllByRole("listitem")).toHaveLength(19);
    fireEvent.click(within(confirm).getByTestId("remove-confirm-all"));
    await waitFor(() => expect(screen.queryByTestId("change-row")).toBeNull());
    expect(screen.queryByTestId("change-gesture-row")).toBeNull();
    // Pending records only: nothing was ever written to the file.
    expect(srv.assertionPuts).toHaveLength(0);
  });
});

// ===========================================================================
// the pin and the plane stamp (LES-296)
// ===========================================================================
describe("S5.1.b -- the clone's pin goes through S4's writer and keeps the plane stamp's rule", () => {
  async function stageDefaultClone(container: HTMLElement): Promise<void> {
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
  }

  it("an EMPTY layout is stamped current: the set is wholly this gesture's", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageDefaultClone(container);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    expect(srv.layoutPuts[0]!.doc["plane"]).toBe(PLANE_STAMP);
  });

  it("a STALE layout stays stale: one clone re-authors one pin, never the operator's others", async () => {
    const stale = "l0-1-00000000";
    const srv = server({ pins: { "site:10.20": { x: 11, y: 22 } }, plane: stale });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("pins-plane-note");
    await stageDefaultClone(container);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    const doc = srv.layoutPuts[0]!.doc;
    expect(doc["plane"]).toBe(stale);
    expect((doc["pins"] as Pins)["site:10.20"]).toEqual({ x: 11, y: 22 });
    expect(Object.keys(doc["pins"] as Pins).sort()).toEqual(["segment:10.80.0.0/24", "site:10.20"]);
  });
});

// ===========================================================================
// acceptance 4, as the operator meets it
// ===========================================================================
describe("S5.1.b -- a refused clone is said while typing, refused on Stage, and stages NOTHING", () => {
  it("an exact observed CIDR", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("clone-cidr"), { target: { value: "10.80.72.0/24" } });
    expect(within(form).getByTestId("clone-refusal").textContent).toMatch(
      /^10\.80\.72\.0\/24 is already declared in the cast — a clone creates a NEW segment.*Stage refuses it\./,
    );
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    expect(within(form).getByTestId("gesture-error").textContent).toMatch(/^10\.80\.72\.0\/24 is already declared in the cast/);
    expect(screen.queryByTestId("change-row")).toBeNull();
    expect(srv.layoutPuts).toHaveLength(0);
    expect(selectionKind()).toBe("clone");
  });

  it("a host collision offers renumber, and renumbered it stages", async () => {
    const file = fileDoc({
      add: [{ kind: "host", key: "address:10.80.0.30", segment: "10.80.72.0/24", note: "saved" }],
    });
    const srv = server({ file });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    expect(within(form).getByTestId("clone-refusal").textContent).toMatch(/^address:10\.80\.0\.30 .* is already claimed by a saved add record/);
    fireEvent.submit(form);
    expect(screen.getAllByTestId("change-row")).toHaveLength(1);

    fireEvent.click(within(form).getByTestId("clone-offer-renumber"));
    expect((within(form).getByTestId("clone-mapping-renumber") as HTMLInputElement).checked).toBe(true);
    fireEvent.submit(form);
    // The saved host stays its own row; the clone is one gesture entry beside
    // it, expanding to its 18 records.
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(1 + 18);
  });
});

// ===========================================================================
// acceptance 7 -- S5.1.c, the site + with a hosts block
// ===========================================================================
describe("S5.1.c acceptance 7 -- the site + generates hosts on a fresh segment", () => {
  async function openSitePlus(site: string): Promise<HTMLElement> {
    const adds = await screen.findAllByTestId("outline-site-add");
    const button = adds.find((b) => b.getAttribute("data-site") === site)!;
    fireEvent.click(button);
    return screen.findByTestId("form-add-segment");
  }

  it("N=3 with a role the persona library serves: add: segment + 3 × add: host at .10/.11/.12, listen prefilled", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    const form = await openSitePlus("site:10.80");
    fireEvent.change(within(form).getByTestId("seg-host-count"), { target: { value: "3" } });
    fireEvent.change(within(form).getByTestId("seg-host-role"), { target: { value: "domain-controller" } });
    expect(within(form).getByTestId("seg-host-listen-source").textContent).toBe(
      // Restated TWICE (LES-299). First for the drive-aware table: a
      // domain-controller's defaults are mostly what its own real-service
      // persona SERVES, so "the ports its personas dial a domain-controller on"
      // was false. Then for review M1 + S7: "listens on" was a claim the table
      // cannot make (a real-service persona's emits carry no direction), and
      // the load-generator personas are now left out.
      "prefilled from the persona library: the ports a domain-controller is reached on there — " +
        "what its own persona serves, and what other personas dial it on (the load-generator " +
        "personas are left out). A real-service persona's emits are filed under its own id, so a " +
        "port the role dials (a web tier's 3306 to the db tier) can appear here; drop what the " +
        "host does not serve.",
    );
    // M1: a role that is NOT one of the five names them, so the operator can
    // recognise the case before typing one.
    expect(within(form).getByTestId("seg-host-listen-caveat").textContent).toBe(
      "The roles this is known to affect: db-replica, egress-proxy, monitoring-collector, protocol-gateway, web-tier.",
    );
    const table = ROLE_LISTEN_DEFAULTS["domain-controller"]!;
    const shown = [...within(within(form).getByTestId("seg-host-listen")).getAllByRole("listitem")].map((li) =>
      (li.textContent ?? "").replace(/\s*×\s*$/, "").trim(),
    );
    expect(shown).toEqual(table.map((e) => `${e.port}/${e.proto}`));
    expect(within(form).getByTestId("seg-host-addresses").textContent).toMatch(/^addresses: 10\.80\.0\.10, 10\.80\.0\.11, 10\.80\.0\.12 —/);

    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "three DCs for the lab" } });
    fireEvent.submit(form);
    const generated = await screen.findByTestId("change-gesture-row");
    expect(within(generated).getByTestId("change-gesture").textContent).toBe("add segment 10.80.0.0/24 with 3 hosts");
    expect(within(generated).getByTestId("gesture-counts").textContent).toBe("1 segment · 3 hosts");
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(4);

    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    const listen = table.map((e) => ({ port: e.port, proto: e.proto }));
    expect(srv.assertionPuts[0]!["add"]).toEqual([
      { kind: "segment", key: "10.80.0.0/24", name: "seg-10-80-0-0", zone_type: "simple", note: "three DCs for the lab" },
      ...["10.80.0.10", "10.80.0.11", "10.80.0.12"].map((a) => ({
        kind: "host",
        key: `address:${a}`,
        segment: "10.80.0.0/24",
        role: "domain-controller",
        listen,
        note: "three DCs for the lab",
      })),
    ]);
  });

  it("skips addresses the export and the file already hold", async () => {
    const withTaken: TopologyExport = {
      ...trimmedExport,
      nodes: [
        ...trimmedExport.nodes,
        { ...trimmedExport.nodes[0]!, id: "address:10.80.0.10", address: "10.80.0.10", segment: null, role: null, listen_ports: [], dial_ports: [] },
      ],
    };
    const file = fileDoc({ add: [{ kind: "host", key: "address:10.80.0.12", segment: "10.80.72.0/24", note: "saved" }] });
    const srv = server({ exportData: withTaken, file });
    open(`/design/${IMPRINT}`, srv.api);
    const form = await openSitePlus("site:10.80");
    fireEvent.change(within(form).getByTestId("seg-host-count"), { target: { value: "3" } });
    expect(within(form).getByTestId("seg-host-addresses").textContent).toMatch(/^addresses: 10\.80\.0\.11, 10\.80\.0\.13, 10\.80\.0\.14 —/);
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(1 + 4);
    const keys = screen.getAllByTestId("change-row").map((r) => r.textContent ?? "");
    expect(keys.some((k) => k.includes("address:10.80.0.11"))).toBe(true);
    expect(keys.some((k) => k.includes("address:10.80.0.13"))).toBe(true);
    expect(keys.some((k) => k.includes("address:10.80.0.14"))).toBe(true);
  });

  it("a role no persona serves prefills an EMPTY listen set and says so -- and the hosts carry no listen", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    const form = await openSitePlus("site:10.80");
    fireEvent.change(within(form).getByTestId("seg-host-count"), { target: { value: "2" } });
    fireEvent.change(within(form).getByTestId("seg-host-role"), { target: { value: "generic-endpoint" } });
    expect(within(form).getByTestId("seg-host-listen-source").textContent).toBe(
      "no persona in the library serves generic-endpoint, so the listen set starts empty",
    );
    expect(within(within(form).getByTestId("seg-host-listen")).queryAllByRole("listitem")).toHaveLength(0);
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(3);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    const hosts = (srv.assertionPuts[0]!["add"] as Array<Record<string, unknown>>).filter((r) => r["kind"] === "host");
    expect(hosts).toHaveLength(2);
    for (const h of hosts) expect("listen" in h).toBe(false);
  });

  it("with no count the site + stages ONE record, exactly as S5.1.a did", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    const form = await openSitePlus("site:10.80");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    expect(screen.queryByTestId("change-gesture")).toBeNull();
  });
});

// ===========================================================================
// the combined fix round -- M1, S1, S2, S3, S5
// ===========================================================================
describe("the prefill says what the table cannot know (M1)", () => {
  it("names the typed role itself when it is one of the five, as a notice", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    const adds = await screen.findAllByTestId("outline-site-add");
    fireEvent.click(adds.find((b) => b.getAttribute("data-site") === "site:10.80")!);
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(within(form).getByTestId("seg-host-count"), { target: { value: "4" } });
    fireEvent.change(within(form).getByTestId("seg-host-role"), { target: { value: "web-tier" } });
    // The prefill offers 3306 -- the port a web tier DIALS on the db tier.
    const shown = within(within(form).getByTestId("seg-host-listen"))
      .getAllByRole("listitem")
      .map((li) => (li.textContent ?? "").replace(/\s*×\s*$/, "").trim());
    expect(shown).toContain("3306/tcp");
    const caveat = within(form).getByTestId("seg-host-listen-caveat");
    expect(caveat.textContent).toBe("web-tier is one of them: check this set before staging.");
    // A NOTICE, not a `meta` aside: this set is about to be written.
    expect(caveat.className).toBe("notice");
    expect(within(form).getByTestId("seg-host-listen-source").textContent).toMatch(
      /a port the role dials \(a web tier's 3306 to the db tier\) can appear here; drop what the host does not serve\.$/,
    );
    // The operator can act on it: dropping 3306 leaves it out of the record.
    fireEvent.click(within(within(form).getByTestId("seg-host-listen")).getByLabelText("remove 3306/tcp"));
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "four web tiers" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    expandGestures();
    expect(screen.getAllByTestId("change-row")).toHaveLength(5);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    const hosts = (srv.assertionPuts[0]!["add"] as Array<Record<string, unknown>>).filter((r) => r["kind"] === "host");
    expect(hosts).toHaveLength(4);
    for (const h of hosts) {
      expect(h["listen"]).toEqual([
        { port: 443, proto: "tcp" },
        { port: 80, proto: "tcp" },
      ]);
    }
  });
});

describe("choose again keeps the form (S1)", () => {
  it("a typed CIDR, a typed note and a ticked mirror survive re-placing", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");

    fireEvent.change(within(form).getByTestId("clone-cidr"), { target: { value: "10.80.44.0/24" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.click(within(form).getByTestId("clone-mirror"));
    fireEvent.click(within(form).getByTestId("clone-hosts-first"));
    fireEvent.change(within(form).getByTestId("clone-first-n"), { target: { value: "2" } });

    // "choose again" -- the control that used to throw the whole form away.
    fireEvent.click(within(form).getByTestId("clone-replace"));
    expect(await screen.findByTestId("clone-placement")).toBeTruthy();
    const still = screen.getByTestId("form-clone");
    expect((within(still).getByTestId("clone-cidr") as HTMLInputElement).value).toBe("10.80.44.0/24");
    expect((within(still).getByTestId("gesture-note") as HTMLInputElement).value).toBe(NOTE);
    expect((within(still).getByTestId("clone-mirror") as HTMLInputElement).checked).toBe(true);
    expect((within(still).getByTestId("clone-first-n") as HTMLInputElement).value).toBe("2");
    // ...and Stage is held back with a reason while there is no placement.
    expect((screen.getByTestId("stage-button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("stage-blocked").textContent).toMatch(/choose where the clone goes first/);

    fireEvent.click(screen.getByTestId("clone-nudge-right"));
    fireEvent.click(screen.getByTestId("clone-place"));
    const back = await screen.findByTestId("form-clone");
    expect((within(back).getByTestId("clone-cidr") as HTMLInputElement).value).toBe("10.80.44.0/24");
    expect((within(back).getByTestId("gesture-note") as HTMLInputElement).value).toBe(NOTE);
    expect((within(back).getByTestId("clone-mirror") as HTMLInputElement).checked).toBe(true);
    expect((within(back).getByTestId("clone-first-n") as HTMLInputElement).value).toBe("2");
    expect((screen.getByTestId("stage-button") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.submit(back);
    // 1 segment + 2 hosts ("first 2") + the 2 mirrored conduits: every field
    // that survived is visible in what was staged, not just in the inputs --
    // and the gesture entry says exactly that in its counts.
    const staged = await screen.findByTestId("change-gesture-row");
    expect(within(staged).getByTestId("gesture-counts").textContent).toBe("1 segment · 2 hosts · 2 conduits");
    expandGestures();
    const keys = screen.getAllByTestId("change-row").map((r) => r.textContent ?? "");
    expect(keys).toHaveLength(5);
    expect(keys.some((k) => k.includes("10.80.44.0/24"))).toBe(true);
    expect(keys.filter((k) => k.includes("|"))).toHaveLength(2);
  });

  it("a NEW source starts a NEW form -- the kept-mounted form is keyed by it (probe R4)", async () => {
    // Keeping a form mounted means giving it an identity (LES-259). An Alt-drag
    // of another tile re-renders `CloneBody` in place for a different gesture,
    // and without the key it inherited this one's CIDR, note and mirror choice
    // under a retitled frame -- which would stage `cloned from <the new
    // source>: <the old note>` on a CIDR chosen for the old one.
    const OTHER = "10.80.72.0/24";
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("clone-cidr"), { target: { value: "10.80.9.0/24" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "A's note" } });
    fireEvent.click(within(form).getByTestId("clone-mirror"));

    // Alt-drag ANOTHER tile, with the first clone's form still open.
    const svg = screen.getByTestId("level0-svg");
    const handle = svg.querySelector(`[data-pinnable="segment:${OTHER}"]`)!;
    fireEvent.pointerDown(handle, { button: 0, pointerId: 11, clientX: 0, clientY: 0, altKey: true });
    fireEvent.pointerMove(svg, { pointerId: 11, clientX: 300, clientY: 180 });
    fireEvent.pointerUp(svg, { pointerId: 11, clientX: 300, clientY: 180 });

    await waitFor(() => expect(title()).toBe(`Clone ${OTHER}`));
    const fresh = await screen.findByTestId("form-clone");
    const cidr = (within(fresh).getByTestId("clone-cidr") as HTMLInputElement).value;
    // NOT the typed one -- the new gesture starts at its own source's default.
    // (Both sources sit in the 10.80 site, so that default is the same CIDR for
    // either; what must not survive is the TYPED value.)
    expect(cidr).not.toBe("10.80.9.0/24");
    expect((within(fresh).getByTestId("gesture-note") as HTMLInputElement).value).toBe("");
    expect((within(fresh).getByTestId("clone-mirror") as HTMLInputElement).checked).toBe(false);
    expect((within(fresh).getByTestId("clone-hosts-all") as HTMLInputElement).checked).toBe(true);

    // Staged, the records name the NEW source in every note and key.
    fireEvent.change(within(fresh).getByTestId("gesture-note"), { target: { value: "B's note" } });
    fireEvent.submit(fresh);
    const fresh2 = await screen.findByTestId("change-gesture-row");
    expect(within(fresh2).getByTestId("change-gesture").textContent).toBe(`clone of ${OTHER} → ${cidr}`);
    expandGestures();
    expect(screen.getAllByTestId("change-row").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    for (const r of srv.assertionPuts[0]!["add"] as Array<Record<string, unknown>>) {
      expect(r["note"]).toBe(`cloned from ${OTHER}: B's note`);
    }
  });

  it("the placed line says the form is kept, so the control no longer reads as position-only", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    expect(within(form).getByTestId("clone-placed").textContent).toMatch(/what is filled in below is kept/);
  });
});

describe("the preview runs the batch checks too (S5)", () => {
  it("a CIDR the file SUPPRESSES is refused in the preview, with no count above it", async () => {
    // `planClone`'s claim check reads the `add` block only (`segmentClaims`), so
    // a suppressed key gets a clean plan -- and then `14` §3's "a key in both
    // add and suppress" refusal stops the whole batch. Exactly the gap the
    // preview used to hide behind "stages 1 segment · 17 hosts · 0 conduits".
    const file = fileDoc({ suppress: [{ kind: "segment", key: "10.80.44.0/24", note: "out of scope" }] });
    const srv = server({ file });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("clone-cidr"), { target: { value: "10.80.44.0/24" } });

    const refusal = await within(form).findByTestId("clone-refusal");
    expect(refusal.textContent).toMatch(
      /^record 1 of \d+ \(add segment 10\.80\.44\.0\/24\): key 10\.80\.44\.0\/24 is already in `suppress`/,
    );
    expect(refusal.textContent).toMatch(/the batch is refused whole, and nothing was staged\. Stage refuses it\.$/);
    // The count that used to sit above it is GONE: it promised what Stage would
    // then refuse.
    expect(within(form).queryByTestId("clone-summary")).toBeNull();

    // And Stage does refuse it, in the same words, staging nothing.
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByTestId("gesture-error").textContent).toBe(refusal.textContent!.replace(/\. Stage refuses it\.$/, "")));
    // Only the file's own record is in the change list.
    expect(screen.getAllByTestId("change-row")).toHaveLength(1);
  });
});

describe("a failed layout write is SAID, and the pin bar counts honestly (S2, S3)", () => {
  it("names the failure, marks the tile, and counts the browser-only placement", async () => {
    const srv = server({
      layoutPutFails: new ConsoleApiError({
        kind: "unavailable",
        status: 503,
        message: "no",
        url: "/api/imprints/x/layout",
      }),
    });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await startCloneByKeyboard(container);
    fireEvent.click(screen.getByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: NOTE } });
    fireEvent.submit(form);

    // The PUT was attempted and failed.
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    const failed = await screen.findByTestId("pins-save-failed");
    expect(failed.textContent).toMatch(/^the position could not be saved \(unavailable\):/);
    expect(failed.getAttribute("role")).toBe("alert");
    // The tile is still drawn -- and says its position is not saved, visibly.
    const tile = addedTile(container, "10.80.0.0/24");
    expect(tile).not.toBeNull();
    expect(screen.getByTestId("added-tile-unsaved").textContent).toBe("position not saved");
    expect(tile!.closest("[data-pinnable]")!.getAttribute("aria-label")).toMatch(
      /placed in this browser only \(its position is not saved\)/,
    );
    // The pin bar counts it as this browser's, and claims no saved pin.
    expect(screen.getByTestId("pins-session").textContent).toMatch(/1 placed in this browser only/);
    expect(screen.getByTestId("pin-controls").textContent).not.toMatch(/\d+ pinned/);
  });

  it("a pin naming no segment in play is disclosed, not counted as a placed tile (S2)", async () => {
    // A layout file left holding a pin for a clone whose records are gone --
    // exactly what "remove all 18" leaves behind.
    const srv = server({ pins: { "segment:10.80.31.0/24": { x: 900, y: 40 } } });
    open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("level0-svg");
    const bar = await screen.findByTestId("pin-controls");
    expect(bar.textContent).toMatch(/1 pinned/);
    expect(screen.getByTestId("pins-orphan").textContent).toMatch(
      /1 naming no segment this twin or this file has/,
    );
    // ...and it really is drawing nothing.
    expect(screen.queryByTestId("added-tile")).toBeNull();
  });

  it("a pin for a segment the file DOES add is not called an orphan", async () => {
    const srv = server({
      pins: { "segment:10.80.31.0/24": { x: 900, y: 40 } },
      file: fileDoc({ add: [{ kind: "segment", key: "10.80.31.0/24", note: "mine" }] }),
    });
    open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("level0-svg");
    await waitFor(() => expect(screen.queryByTestId("added-tile")).not.toBeNull());
    expect(screen.queryByTestId("pins-orphan")).toBeNull();
    // Its position came from the FILE, so it is not a browser-only placement.
    expect(screen.queryByTestId("pins-session")).toBeNull();
    expect(screen.queryByTestId("added-tile-unsaved")).toBeNull();
  });
});
