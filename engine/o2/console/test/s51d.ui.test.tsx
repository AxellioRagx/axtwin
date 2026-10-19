/**
 * S5.1.d and S5.1.e through the REAL shell (`TestApp` -> `ConsoleShell` ->
 * `ImprintWorkspace`), against a stateful fake API and jsdom's own
 * `localStorage` -- so the draft is exercised where it actually lives.
 *
 *   - acceptance 1: a pending clone and a generated segment draw hatched --
 *     placed at their pin, UNPLACED in their site column, held and disclosed
 *     when the group is collapsed, and their hosts as hatched chips at level 1;
 *   - acceptance 3: one change-list entry per gesture, with the counts, the
 *     expander and the tag;
 *   - acceptance 4: undo of a gesture removes exactly its batch, drops its pin,
 *     and re-saves once when saved records were involved;
 *   - acceptance 5: *Add host here* prefills the segment and the next free
 *     address (S5.1.e);
 *   - acceptance 6: the draft survives a reload and an imprint switch, is
 *     discarded on save, reconciles a stale one visibly, and degrades with a
 *     note when storage is blocked;
 *   - acceptance 7: every exit from an imprint with pending edits is guarded.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type {
  AssertionsDoc,
  CastSummary,
  ImprintList,
  ImprintProvenance,
  LayoutDoc,
  TopologyExport,
} from "../src/api/types";
import { DRAFT_SCHEMA, draftKey } from "../src/edit/draft";
import type { Pins } from "../src/layout/pins";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const IMPRINT = "trimmed-fixture";
const OTHER = "other-bundle";
const SOURCE = "10.80.20.0/24";
const NOTE = "a second lab floor";

function castFor(exportData: TopologyExport, id: string, fingerprint: string | null = "fp-1"): CastSummary {
  return {
    id,
    bundle_dir: `/b/${id}`,
    twin_config_id: id,
    description: null,
    pit_id: "pit-1",
    bundle_schema: "axr-topology/1.5",
    topology_schema_version: "1.3",
    // The PIT stamp is held CONSTANT: it does not move on a re-cast, so a
    // draft check keyed on it could never fire (fix round 1, GAP-087..090).
    // What moves is the bundle's CONTENT fingerprint.
    code_fingerprint: "pit-stamp-constant",
    bundle_fingerprint: fingerprint,
    params: {},
    deploy_min_occupancy: trimmedCast.deployMinOccupancy,
    deploy_min_occupancy_source: "declared",
    segments: exportData.segments.length,
    node_specs: 40,
    vms: 40,
    hosts: 40,
    conduits: (trimmedCast.conduits ?? []).length,
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

function provenanceFor(id: string): ImprintProvenance {
  return {
    imprint_id: id,
    rows: [],
    sidecar_shape: "absent",
    sidecar_schema: null,
    evidence_source: "none",
    code_fingerprint: null,
    population: {},
    warnings: [],
  };
}

function fileDoc(id: string, overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: id,
    path: `/b/${id}/${id}.assertions.yaml`,
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: id,
    as_of: "2026-09-16T00:00:00Z",
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
  /** The layout the fake currently holds -- the pin set a test can read back. */
  pins: () => Pins;
  /** Let a held `GET .../cast` resolve (M3). */
  releaseCast: () => void;
}

/** A fake with a FILE and a LAYOUT per imprint, and a second imprint to switch
 *  to, so the draft's per-imprint scoping is driveable. */
function server(opts: {
  exportData?: TopologyExport;
  file?: AssertionsDoc;
  pins?: Pins;
  fingerprint?: string | null;
  /** Hold `GET .../cast` open until the returned release is called (M3) -- the
   *  realistic order is the cast resolving AFTER the assertions, and a fake
   *  that resolves at once cannot test what happens in that window
   *  (LES-323). */
  holdCast?: boolean;
  /** Fail `GET .../cast` outright, so the recompile check cannot be made. */
  castFails?: boolean;
} = {}): Server {
  let releaseCast: (() => void) | null = null;
  const castGate = opts.holdCast
    ? new Promise<void>((resolve) => {
        releaseCast = resolve;
      })
    : Promise.resolve();
  const exportData = opts.exportData ?? trimmedExport;
  const files: Record<string, AssertionsDoc> = {
    [IMPRINT]: opts.file ?? fileDoc(IMPRINT),
    [OTHER]: fileDoc(OTHER),
  };
  let layout: LayoutDoc = {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/layout.json`,
    exists: opts.pins !== undefined,
    etag: "layout-1",
    schema: "axr-layout/1",
    imprint: IMPRINT,
    pins: { ...(opts.pins ?? {}) },
    plane: null,
  };
  let writes = 1;
  const assertionPuts: Server["assertionPuts"] = [];
  const layoutPuts: Server["layoutPuts"] = [];
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const api: ConsoleApi = {
    listImprints: () =>
      Promise.resolve({
        imprints: [IMPRINT, OTHER].map((id) => ({
          id,
          hosts: 40,
          segments: 6,
          as_of: "",
          mean_confidence: 0,
          has_provenance: false,
          confidence_source: "absent" as const,
        })),
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: (id) => Promise.resolve(provenanceFor(id)),
    getTopology: () => Promise.resolve(exportData),
    getCast: (id) =>
      castGate.then(() => {
        if (opts.castFails) {
          return Promise.reject(
            new ConsoleApiError({ kind: "unavailable", status: 503, message: "no", url: "/api/cast" }),
          ) as Promise<CastSummary>;
        }
        return castFor(exportData, id, opts.fingerprint === undefined ? "fp-1" : opts.fingerprint);
      }),
    getAssertions: (id) => Promise.resolve(files[id] ?? fileDoc(id)),
    putAssertions: (id, document) => {
      assertionPuts.push(document);
      writes += 1;
      files[id] = fileDoc(id, {
        etag: `etag-${writes}`,
        add: (document["add"] as AssertionsDoc["add"]) ?? [],
        override: (document["override"] as AssertionsDoc["override"]) ?? [],
        suppress: (document["suppress"] as AssertionsDoc["suppress"]) ?? [],
      });
      return Promise.resolve(files[id]!);
    },
    getLayout: () => Promise.resolve(layout),
    putLayout: (_id, doc, etag) => {
      layoutPuts.push({ doc, etag });
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
  return {
    api,
    assertionPuts,
    layoutPuts,
    pins: () => layout.pins,
    releaseCast: () => releaseCast?.(),
  };
}

/**
 * Render the app at `url`, UNMOUNTING whatever was rendered before.
 *
 * Several tests here simulate a reload or a navigation by rendering again, and
 * two live apps would both report a pending count, both answer to every test
 * id, and both keep a `beforeunload` listener -- so the second render must
 * genuinely replace the first, exactly as a reload does.
 */
let mounted: ReturnType<typeof render> | null = null;
function open(url: string, api: ConsoleApi): ReturnType<typeof render> {
  mounted?.unmount();
  mounted = render(
    <TestApp client={api} url={url}>
      <ConsoleRoutes />
    </TestApp>,
  );
  return mounted;
}

/** Stage a clone of `SOURCE` through the inspector, keyboard only. */
function gestureRows(): HTMLElement[] {
  return screen.queryAllByTestId("change-gesture-row");
}

async function stageClone(container: HTMLElement, note = NOTE): Promise<void> {
  await screen.findByTestId("level0-svg");
  const before = gestureRows().length;
  const rect = container.querySelector(`[data-testid="tile-actions"][data-cidr="${SOURCE}"]`)!;
  fireEvent.click(rect.parentElement!);
  await screen.findByTestId("action-open-clone");
  fireEvent.click(screen.getByTestId("action-open-clone"));
  fireEvent.click(await screen.findByTestId("clone-place"));
  const form = await screen.findByTestId("form-clone");
  fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: note } });
  fireEvent.submit(form);
  // A COUNT increase, never "an element exists": a second gesture staged
  // beside a first would satisfy the latter before it had staged anything.
  await waitFor(() => expect(gestureRows().length).toBe(before + 1));
}

/** Stage a segment with N generated hosts from a site `+`. With N = 0 it is a
 *  single record, so it stays one plain row rather than a gesture entry. */
async function generateSegment(site: string, hosts: number): Promise<void> {
  const adds = await screen.findAllByTestId("outline-site-add");
  fireEvent.click(adds.find((b) => b.getAttribute("data-site") === site)!);
  const form = await screen.findByTestId("form-add-segment");
  if (hosts > 0) {
    fireEvent.change(within(form).getByTestId("seg-host-count"), { target: { value: String(hosts) } });
  }
  const id = hosts > 0 ? "change-gesture-row" : "change-row";
  const before = screen.queryAllByTestId(id).length;
  fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "a generated floor" } });
  fireEvent.submit(form);
  await waitFor(() => expect(screen.queryAllByTestId(id).length).toBe(before + 1));
}

function addedTile(container: HTMLElement, cidr: string): Element | null {
  return container.querySelector(`[data-testid="added-tile"][data-cidr="${cidr}"]`);
}

/**
 * Seed a PENDING segment with `hosts` hosts directly into this browser's draft.
 *
 * Used where a test needs pending records at a URL it opens cold. Staging them
 * and then re-opening would work -- that IS how the draft carries them -- but it
 * would make acceptance 1 depend on acceptance 6 by accident; seeding says the
 * dependency out loud instead.
 */
function seedDraft(cidr: string, hosts: number, over: { fingerprint?: string | null } = {}): void {
  const gesture = { id: "seed-1", label: `add segment ${cidr} with ${hosts} hosts`, kind: "generate" as const };
  const entries: Array<Record<string, unknown>> = [
    { block: "add", record: { kind: "segment", key: cidr, name: "lab", note: "seeded" }, gesture },
  ];
  for (let i = 0; i < hosts; i++) {
    entries.push({
      block: "add",
      record: { kind: "host", key: `address:${cidr.replace(".0/24", `.${10 + i}`)}`, segment: cidr, note: "seeded" },
      gesture,
    });
  }
  window.localStorage.setItem(
    draftKey(IMPRINT),
    JSON.stringify({
      schema: DRAFT_SCHEMA,
      imprint: IMPRINT,
      saved_at: "2026-09-16T10:00:00Z",
      etag: "etag-1",
      bundle_fingerprint: over.fingerprint === undefined ? "fp-1" : over.fingerprint,
      entries,
    }),
  );
}

/** Let every queued microtask and timer-free effect settle. */
async function drained(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function delay(ms: number): Promise<void> {
  return act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, ms));
  });
}

/** The fingerprint the stored draft carries now, or `null` when there is none. */
function storedFingerprint(): string | null {
  const raw = window.localStorage.getItem(draftKey(IMPRINT));
  return raw === null ? null : (JSON.parse(raw) as { bundle_fingerprint: string | null }).bundle_fingerprint;
}

/** Expand every gesture group, so a test can count the RECORDS. */
function expandGestures(): void {
  for (const b of screen.queryAllByTestId("gesture-expand")) fireEvent.click(b);
}

beforeEach(() => {
  mounted = null;
  window.localStorage.clear();
});

// ===========================================================================
// acceptance 1 -- pending is visible on the map
// ===========================================================================
describe("acceptance 1 -- a pending clone and a generated segment both draw HATCHED", () => {
  it("an UNPLACED generated segment is drawn in its site column, hatched, and selectable", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await generateSegment("site:10.80", 2);

    const tile = await waitFor(() => {
      const t = addedTile(container, "10.80.0.0/24");
      expect(t).not.toBeNull();
      return t!;
    });
    // HATCHED, by the class that decides it and the attribute it carries --
    // a stylesheet rule that set `fill` would beat the attribute and no jsdom
    // assertion could see it (LES-327), which `pending.test.ts` guards.
    expect(tile.getAttribute("class")).toBe("added-tile is-pending");
    expect(tile.getAttribute("fill")).toMatch(/^url\(#/);
    expect(tile.getAttribute("data-state")).toBe("pending");
    // It has NO pin and no session placement: its position is the map's.
    expect(srv.layoutPuts).toHaveLength(0);
    const group = tile.closest("[data-pinnable]")!;
    expect(group.getAttribute("aria-label")).toMatch(/^Pending added segment: segment 10\.80\.0\.0\/24/);
    expect(group.getAttribute("aria-label")).toMatch(/2 hosts staged/);
    // Selecting it opens the added segment's actions.
    fireEvent.click(group);
    expect(await screen.findByTestId("added-segment-facts")).toBeTruthy();
    expect(screen.getByTestId("added-segment-note").textContent).toMatch(/appended to its site's column/);
  });

  it("a PLACED clone keeps drawing at its pin, and an unplaced one appears beside it", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    const placed = addedTile(container, "10.80.0.0/24")!;
    const at = srv.pins()["segment:10.80.0.0/24"]!;
    expect(placed.getAttribute("x")).toBe(String(at.x));
    expect(placed.closest("[data-pinnable]")!.getAttribute("aria-label")).toMatch(/Pending clone of 10\.80\.20\.0\/24/);
  });

  it("an unplaced one whose group the budget COLLAPSED is held and disclosed, and drawn on ?expand=", async () => {
    const srv = server({ exportData: withOverBudgetSite() });
    seedDraft("10.80.0.0/24", 1);
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await waitFor(() => expect(screen.getByTestId("budget-note").textContent).toMatch(/held in 10\.80/));
    expect(addedTile(container, "10.80.0.0/24")).toBeNull();
    expect(screen.getByTestId("budget-note").textContent).toMatch(/1 pending added segment held in 10\.80/);

    // Opened, it is drawn.
    seedDraft("10.80.0.0/24", 1);
    const opened = open(`/design/${IMPRINT}?expand=site%3A10.80`, srv.api);
    await screen.findByTestId("level0-svg");
    await waitFor(() => expect(addedTile(opened.container, "10.80.0.0/24")).not.toBeNull());
  });

  it("the hosts of a PENDING segment are HATCHED CHIPS at level 1, around a hatched spine", async () => {
    seedDraft("10.80.0.0/24", 2);
    open(`/design/${IMPRINT}/segments/10.80.0.0-24`, server().api);
    const chips = await screen.findAllByTestId("pending-chip");
    expect(chips).toHaveLength(2);
    const spine = screen.getByTestId("added-spine");
    expect(spine.getAttribute("class")).toBe("added-tile is-pending");
    expect(spine.getAttribute("fill")).toMatch(/^url\(#/);
    for (const chip of chips) {
      expect(chip.getAttribute("class")).toBe("added-chip is-pending");
      expect(chip.getAttribute("fill")).toMatch(/^url\(#/);
      expect(chip.parentElement!.getAttribute("aria-label")).toMatch(/this file adds it; it is not in the observed topology/);
    }
    // The observed level 1 is not drawn for it: there is no observed segment.
    expect(screen.queryByTestId("spine")).toBeNull();
  });

  it("a host added to an OBSERVED segment is a hatched chip beside its observed bands", async () => {
    const file = fileDoc(IMPRINT, {
      add: [{ kind: "host", key: "address:10.80.20.200", segment: SOURCE, role: "file-server", note: "mine" }],
    });
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, server({ file }).api);
    const chips = await screen.findAllByTestId("pending-chip");
    expect(chips).toHaveLength(1);
    // SAVED, so outlined rather than hatched -- pending is one state of three.
    expect(chips[0]!.getAttribute("class")).toBe("added-chip is-saved");
    expect(chips[0]!.getAttribute("fill")).toBeNull();
    // RESTATED (review S6): the band uses the project's OWN wording for the
    // state (`ChangeList`'s `STATE_LABEL`) -- "added by this file" claimed the
    // file held a record that may be browser-only.
    expect(screen.getByTestId("pending-band").textContent).toMatch(/added here · 1 · saved \(in the file\)/);
    // The observed spine is still drawn: this is an EXTRA band, not a swap.
    expect(screen.getByTestId("spine")).toBeTruthy();
  });
});

// ===========================================================================
// acceptance 3 -- the change list counts gestures
// ===========================================================================
describe("acceptance 3 -- one change-list entry per gesture", () => {
  it("shows the label, the counts, the state and the tag, and expands to its records", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);

    const row = screen.getByTestId("change-gesture-row");
    expect(within(row).getByTestId("change-gesture").textContent).toBe(`clone of ${SOURCE} → 10.80.0.0/24`);
    expect(within(row).getByTestId("gesture-counts").textContent).toBe("1 segment · 17 hosts");
    expect(within(row).getByTestId("change-state").textContent).toBe("pending (browser only)");
    expect(within(row).getByTestId("change-tag").textContent).toMatch(/te deploy --update/);
    // Collapsed by default -- 18 rows would bury every other edit.
    expect(screen.queryAllByTestId("change-row")).toHaveLength(0);
    const expander = within(row).getByTestId("gesture-expand");
    expect(expander.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(expander);
    expect(expander.getAttribute("aria-expanded")).toBe("true");
    const records = screen.getAllByTestId("change-row");
    expect(records).toHaveLength(18);
    // Each record keeps its own remove control (S5.1.d keeps single removal).
    expect(within(records[0]!).getByTestId("change-remove")).toBeTruthy();
    // ...and does not repeat the gesture label under its own header.
    expect(within(records[0]!).queryByTestId("change-gesture")).toBeNull();
  });

  it("a SOLO record stays one plain row, with no expander", async () => {
    const srv = server();
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "one host" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    expect(screen.queryByTestId("change-gesture-row")).toBeNull();
    expect(screen.queryByTestId("gesture-expand")).toBeNull();
  });
});

// ===========================================================================
// acceptance 4 -- undo of a gesture
// ===========================================================================
describe("acceptance 4 -- undo removes exactly the batch, and its pin with it", () => {
  it("removes all 18 records, drops the pin, and leaves the file untouched", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    expect(Object.keys(srv.pins())).toEqual(["segment:10.80.0.0/24"]);

    fireEvent.click(within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo"));
    await waitFor(() => expect(screen.queryByTestId("change-gesture-row")).toBeNull());
    expect(screen.getByTestId("change-list-empty")).toBeTruthy();
    // The PIN goes with the gesture -- iteration 8's disclosed residual.
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(2));
    expect(srv.pins()).toEqual({});
    // Nothing was drawn for it any more...
    expect(addedTile(container, "10.80.0.0/24")).toBeNull();
    // ...and the assertions file was never written: these were pending.
    expect(srv.assertionPuts).toHaveLength(0);
  });

  it("undoes ONLY its own batch, leaving another gesture and a solo record alone", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    await generateSegment("site:10.20", 2);
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(2));

    const clone = screen.getAllByTestId("change-gesture-row").find((r) => r.textContent?.includes("clone of"))!;
    fireEvent.click(within(clone).getByTestId("gesture-undo"));
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    const left = screen.getByTestId("change-gesture-row");
    expect(within(left).getByTestId("change-gesture").textContent).toMatch(/^add segment 10\.20\./);
    expect(within(left).getByTestId("gesture-counts").textContent).toBe("1 segment · 2 hosts");
    expect(container).toBeTruthy();
  });

  it("re-saves ONCE when the gesture's records were saved, and leaves the document as it was before", async () => {
    const srv = server();
    // A record that predates the gesture, so "before" is not the empty file.
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const hostForm = await screen.findByTestId("form-add-host");
    fireEvent.change(within(hostForm).getByTestId("gesture-note"), { target: { value: "mine" } });
    fireEvent.submit(hostForm);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    // The DOCUMENT, without the write envelope's `etag` -- that is the PUT's
    // concurrency guard and advances on every write by design; it is not part
    // of the assertions file (pinned by `batch.roundtrip.test.ts` and
    // `tests/test_api_console.py`).
    const body = (doc: Record<string, unknown> | undefined): string =>
      JSON.stringify({ ...(doc ?? {}), etag: undefined });
    const before = body(srv.assertionPuts[0]);

    // Now the clone, saved too. A fresh render (`open` unmounts the previous
    // one), so the container has to come from it.
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(2));

    // Undo the whole gesture: ONE re-save, and the document is byte-identical
    // to the one from before the gesture existed (S5.1's own clause).
    fireEvent.click(within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(3));
    expect(body(srv.assertionPuts[2])).toBe(before);
    await waitFor(() => expect(screen.queryByTestId("change-gesture-row")).toBeNull());
    // ONE re-save, not one per record.
    expect(srv.assertionPuts).toHaveLength(3);
  });

  it("a gesture that would STRAND a record outside it asks first, and names what goes too", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await generateSegment("site:10.80", 1);
    // A host staged on the generated segment by a SEPARATE gesture: undoing
    // the batch would leave it with no segment. (`open` is a RELOAD here --
    // heavier than the app's own level-1 drill, which keeps the workspace
    // mounted -- so the generated records come back through the draft, which
    // is the behaviour acceptance 6 pins.)
    open(`/design/${IMPRINT}/segments/10.80.0.0-24`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    fireEvent.change(within(form).getByTestId("host-key"), { target: { value: "address:10.80.0.99" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "by hand" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));

    fireEvent.click(within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo"));
    const confirm = await screen.findByTestId("remove-confirm");
    expect(confirm.textContent).toMatch(/would leave with no segment/);
    expect(within(confirm).getAllByRole("listitem")).toHaveLength(1);
    expect(within(confirm).getByTestId("remove-confirm-list").textContent).toMatch(/address:10\.80\.0\.99/);
    expect(within(confirm).getByTestId("remove-confirm-all").textContent).toBe("remove all 3");
    fireEvent.click(within(confirm).getByTestId("remove-confirm-all"));
    await waitFor(() => expect(screen.getByTestId("change-list-empty")).toBeTruthy());
    expect(container).toBeTruthy();
  });

  it("a gesture that strands NOTHING asks nothing", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.click(within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo"));
    expect(screen.queryByTestId("remove-confirm")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("change-list-empty")).toBeTruthy());
  });
});

// ===========================================================================
// acceptance 5 -- S5.1.e
// ===========================================================================
describe("acceptance 5 -- *Add host here* prefills the segment and the next free address", () => {
  it("prefills the next free address, and FOLLOWS it as records are staged and removed", async () => {
    const srv = server();
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    await screen.findByTestId("form-add-host");
    // Re-queried each time, never captured: a remount would leave a captured
    // node detached and frozen at whatever it last showed (LES-323's family).
    const form = (): HTMLElement => screen.getByTestId("form-add-host");
    const key = (): string => (within(form()).getByTestId("host-key") as HTMLInputElement).value;

    // `.10` is NOT free: the fixture has a HOSTNAME-keyed node whose `address`
    // is `10.80.20.10`, and `takenAddresses` reads the address field as well as
    // the `address:` key -- which is the whole point of it. So the first free
    // address from `.10` upward is `.11`.
    expect(key()).toBe("address:10.80.20.11");
    expect(within(form()).getByTestId("host-key-default").textContent).toMatch(/next free address in 10\.80\.20\.0\/24/);

    fireEvent.change(within(form()).getByTestId("gesture-note"), { target: { value: "one" } });
    fireEvent.submit(form());
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    // It MOVED ON -- not back to the address just staged (LES-322).
    await waitFor(() => expect(key()).toBe("address:10.80.20.12"));

    // ...and back again when the record is removed.
    const row = screen.getAllByTestId("change-row")[0]!;
    const remove = within(row).getByTestId("change-remove") as HTMLButtonElement;
    expect(remove.disabled).toBe(false);
    fireEvent.click(remove);
    await waitFor(() => expect(screen.queryAllByTestId("change-row")).toHaveLength(0));
    await waitFor(() => expect(key()).toBe("address:10.80.20.11"));
  });

  it("what the operator TYPED is never overwritten by a moving suggestion", async () => {
    const srv = server();
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    fireEvent.change(within(form).getByTestId("host-key"), { target: { value: "hostname:printer-1" } });
    expect(screen.queryByTestId("host-key-default")).toBeNull();
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "a printer" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    expect(screen.getAllByTestId("change-row")[0]!.textContent).toMatch(/hostname:printer-1/);
  });

  it("on an ADDED segment it prefills that segment's own first address", async () => {
    const srv = server();
    open(`/design/${IMPRINT}`, srv.api);
    await generateSegment("site:10.20", 0);
    const cidr = screen.getByTestId("change-row").textContent!.match(/10\.20\.\d+\.0\/24/)![0];
    // A RELOAD at the added segment's own route: the pending add returns
    // through the draft (acceptance 6), and the form prefills from there.
    open(`/design/${IMPRINT}/segments/${cidr.replace("/", "-")}`, srv.api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    expect((within(form).getByTestId("host-key") as HTMLInputElement).value).toBe(`address:${cidr.replace(".0/24", ".10")}`);
  });
});

// ===========================================================================
// acceptance 6 -- the draft
// ===========================================================================
describe("acceptance 6 -- the draft survives, reconciles, and degrades honestly", () => {
  it("survives a RELOAD of the same URL, says when it was taken, and offers to discard it", async () => {
    const srv = server();
    const first = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(first.container);
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();
    first.unmount();

    // A reload: a brand-new render of the same URL, nothing carried in memory.
    open(`/design/${IMPRINT}`, srv.api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/^Restored 18 unsaved edits from a draft from /);
    expect(notice.getAttribute("role")).toBe("status");
    const row = screen.getByTestId("change-gesture-row");
    // The GESTURE came back too, not 18 loose records.
    expect(within(row).getByTestId("change-gesture").textContent).toBe(`clone of ${SOURCE} → 10.80.0.0/24`);
    expect(within(row).getByTestId("gesture-counts").textContent).toBe("1 segment · 17 hosts");

    fireEvent.click(screen.getByTestId("draft-discard"));
    await waitFor(() => expect(screen.getByTestId("change-list-empty")).toBeTruthy());
    expect(window.localStorage.getItem(draftKey(IMPRINT))).toBeNull();
  });

  it("survives leaving the imprint and coming back, and is NOT carried into the other imprint", async () => {
    const srv = server();
    const view = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(view.container);
    view.unmount();

    // The other imprint: its own (empty) draft, and no notice.
    open(`/design/${OTHER}`, srv.api);
    await screen.findByTestId("edit-panel");
    expect(screen.queryByTestId("draft-restored")).toBeNull();
    expect(screen.getByTestId("change-list-empty")).toBeTruthy();
    screen.getByTestId("edit-panel");
    expect(window.localStorage.getItem(draftKey(OTHER))).toBeNull();
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();
  });

  it("is DISCARDED when its records are saved", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    // The pending set is empty, so the draft is gone -- self-healing, rather
    // than a separate "clear the draft" step that could be forgotten.
    await waitFor(() => expect(window.localStorage.getItem(draftKey(IMPRINT))).toBeNull());
  });

  it("a STALE draft is reconciled VISIBLY: what the file now holds is dropped, and what moved is said", async () => {
    // The file already holds one of the draft's records, and the bundle has a
    // different compiler build than when the draft was taken.
    const held = { kind: "host" as const, key: "address:10.80.90.10", segment: "10.80.90.0/24", note: "saved by someone else" };
    window.localStorage.setItem(
      draftKey(IMPRINT),
      JSON.stringify({
        schema: DRAFT_SCHEMA,
        imprint: IMPRINT,
        saved_at: "2026-09-15T09:00:00Z",
        etag: "etag-old",
        bundle_fingerprint: "fp-OLD",
        entries: [
          { block: "add", record: { kind: "segment", key: "10.80.90.0/24", name: "lab", note: "mine" } },
          { block: "add", record: held },
        ],
      }),
    );
    const srv = server({ file: fileDoc(IMPRINT, { add: [held] }) });
    open(`/design/${IMPRINT}`, srv.api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/^Restored 1 unsaved edit from a draft from 2026-09-15T09:00:00Z/);
    expect(notice.textContent).toMatch(/1 record was left out: add:address:10\.80\.90\.10 — the file already holds this record/);
    expect(notice.textContent).toMatch(/The bundle has been recompiled since this draft was taken/);
    // The file's own record is untouched and still SAVED; the draft's copy of
    // it was never re-staged over it.
    const rows = screen.getAllByTestId("change-row");
    const saved = rows.find((r) => r.textContent?.includes("address:10.80.90.10"))!;
    expect(saved.getAttribute("data-state")).toBe("saved");
    expect(saved.textContent).toMatch(/saved by someone else|saved \(in the file\)/);
  });

  it("a BLOCKED store says so once, names what is at risk, and does not take the app down", async () => {
    const boom = new Error("The operation is insecure.");
    const spy = vi.spyOn(window.localStorage.__proto__, "setItem").mockImplementation(() => {
      throw boom;
    });
    try {
      const srv = server();
      const { container } = open(`/design/${IMPRINT}`, srv.api);
      await stageClone(container);
      const problem = await screen.findByTestId("draft-problem");
      expect(problem.textContent).toMatch(/these 18 unsaved edits are NOT being kept as a draft/);
      expect(problem.textContent).toMatch(/The operation is insecure/);
      expect(problem.textContent).toMatch(/they will be lost if you leave or reload/);
      expect(problem.getAttribute("role")).toBe("alert");
      // The edits themselves are fine: the app kept working.
      expect(screen.getByTestId("change-gesture-row")).toBeTruthy();
    } finally {
      spy.mockRestore();
    }
  });

  it("a draft for ANOTHER imprint is never rehydrated into this one", async () => {
    window.localStorage.setItem(
      draftKey(IMPRINT),
      JSON.stringify({
        schema: DRAFT_SCHEMA,
        imprint: OTHER,
        saved_at: "2026-09-15T09:00:00Z",
        etag: null,
        bundle_fingerprint: null,
        entries: [{ block: "add", record: { kind: "segment", key: "10.80.90.0/24", name: "lab", note: "mine" } }],
      }),
    );
    open(`/design/${IMPRINT}`, server().api);
    await screen.findByTestId("edit-panel");
    expect(screen.getByTestId("change-list-empty")).toBeTruthy();
    expect((await screen.findByTestId("draft-problem")).textContent).toMatch(/not a .* draft for this imprint/);
  });
});

// ===========================================================================
// acceptance 7 -- every exit is guarded
// ===========================================================================
describe("acceptance 7 -- every way out of an imprint with pending edits is guarded", () => {
  async function withPendingEdits(): Promise<Server> {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    return srv;
  }

  it("the BREADCRUMB is guarded, names the count, and says the work is kept", async () => {
    await withPendingEdits();
    const crumb = within(screen.getByLabelText("Breadcrumb")).getByText("imprints");
    fireEvent.click(crumb);
    const guard = await screen.findByTestId("leave-guard");
    expect(guard.textContent).toMatch(/^18 unsaved edits in this imprint\./);
    expect(guard.textContent).toMatch(/kept as a draft on this browser/);
    expect(guard.getAttribute("role")).toBe("alert");
    // Still here.
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
    // Stay: the guard goes, the imprint stays.
    fireEvent.click(screen.getByTestId("leave-stay"));
    await waitFor(() => expect(screen.queryByTestId("leave-guard")).toBeNull());
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
  });

  it("*Leave anyway* goes exactly where the click was going", async () => {
    await withPendingEdits();
    fireEvent.click(within(screen.getByLabelText("Breadcrumb")).getByText("imprints"));
    fireEvent.click(await screen.findByTestId("leave-anyway"));
    await waitFor(() => expect(screen.queryByTestId("level0-svg")).toBeNull());
    // ...and the work is on the browser, as the guard promised.
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();
  });

  it("the OUTLINE's imprint links are guarded", async () => {
    await withPendingEdits();
    const other = screen.getAllByTestId("outline-imprint").find((a) => a.getAttribute("data-imprint") === OTHER)!;
    fireEvent.click(other);
    expect(await screen.findByTestId("leave-guard")).toBeTruthy();
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
  });

  it("ESCAPE refuses without offering a one-click exit -- a keypress is cheap to make by accident", async () => {
    await withPendingEdits();
    // Clear the selection the clone left, so Escape reaches "up a level".
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Escape" });
    const status = await screen.findByTestId("inspector-status");
    expect(status.textContent).toMatch(/18 unsaved edits — save or discard them before leaving this imprint/);
    expect(screen.queryByTestId("leave-anyway")).toBeNull();
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
  });

  it("the BROWSER's own navigation is guarded, and only while there is something to lose", async () => {
    // With nothing pending, `beforeunload` must not fire at all: a prompt on
    // every reload trains the operator to dismiss it.
    const srv = server();
    const clean = open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("level0-svg");
    const quiet = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(quiet);
    expect(quiet.defaultPrevented).toBe(false);
    clean.unmount();

    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    const loud = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(loud);
    expect(loud.defaultPrevented).toBe(true);
  });

  it("a navigation WITHIN the imprint is never guarded", async () => {
    await withPendingEdits();
    // The level-1 drill of an observed segment: same imprint, same store.
    const crumbs = screen.getByLabelText("Breadcrumb");
    expect(crumbs).toBeTruthy();
    const tile = document.querySelector(`[data-testid="segment-tile"]`)!;
    fireEvent.click(tile.closest("[data-pinnable]")!);
    expect(screen.queryByTestId("leave-guard")).toBeNull();
  });

  it("with the edits SAVED, every exit is quiet again", async () => {
    const srv = await withPendingEdits();
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    // The PUT going out is not the state settling: wait for the gesture to be
    // SAVED, which is when there is nothing pending left to guard.
    await waitFor(() =>
      expect(screen.getByTestId("change-gesture-row").getAttribute("data-state")).toBe("saved"),
    );
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    fireEvent.click(within(screen.getByLabelText("Breadcrumb")).getByText("imprints"));
    await waitFor(() => expect(screen.queryByTestId("level0-svg")).toBeNull());
    expect(screen.queryByTestId("leave-guard")).toBeNull();
  });
});

// ===========================================================================
// the combined fix round -- M2, M3, S1, S4, S5, S9, N2
// ===========================================================================
describe("a restored batch and a new one stay SEPARATE gestures (M2)", () => {
  it("restore, then stage a second clone: two rows, two undos, two pins", async () => {
    const srv = server();
    const first = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(first.container);
    const firstId = screen.getByTestId("change-gesture-row").getAttribute("data-gesture")!;
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));

    // A reload restores it -- and then a SECOND clone is staged. A per-mount
    // counter restarts at `gesture-1` here and merges the two.
    const back = open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("draft-restored");
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    const restoredId = screen.getByTestId("change-gesture-row").getAttribute("data-gesture")!;
    expect(restoredId).toBe(firstId);

    // A different CIDR, so the second batch is not refused as a duplicate.
    await screen.findByTestId("level0-svg");
    const rect = back.container.querySelector(`[data-testid="tile-actions"][data-cidr="10.80.72.0/24"]`)!;
    fireEvent.click(rect.parentElement!);
    fireEvent.click(await screen.findByTestId("action-open-clone"));
    fireEvent.click(await screen.findByTestId("clone-place"));
    const form = await screen.findByTestId("form-clone");
    fireEvent.change(within(form).getByTestId("clone-cidr"), { target: { value: "10.80.44.0/24" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the second one" } });
    fireEvent.submit(form);

    // TWO rows, with DIFFERENT ids and their own counts.
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(2));
    const rows = screen.getAllByTestId("change-gesture-row");
    const ids = rows.map((r) => r.getAttribute("data-gesture"));
    expect(new Set(ids).size).toBe(2);
    expect(ids).toContain(restoredId);

    // Undoing the NEW one leaves the restored one whole.
    const second = rows.find((r) => r.getAttribute("data-gesture") !== restoredId)!;
    fireEvent.click(within(second).getByTestId("gesture-undo"));
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
    const left = screen.getByTestId("change-gesture-row");
    expect(left.getAttribute("data-gesture")).toBe(restoredId);
    expect(within(left).getByTestId("gesture-counts").textContent).toBe("1 segment · 17 hosts");
    // ...and the restored clone's pin survives while the new one's goes.
    await waitFor(() => expect(Object.keys(srv.pins())).toEqual(["segment:10.80.0.0/24"]));
  });
});

describe("the recompile warning waits for the CAST (M3)", () => {
  it("is reported when the cast resolves AFTER the assertions, not skipped", async () => {
    seedDraft("10.80.90.0/24", 1, { fingerprint: "fp-OLD" });
    const srv = server({ holdCast: true, fingerprint: "fp-NEW" });
    open(`/design/${IMPRINT}`, srv.api);
    // The assertions have landed; the cast has not. Nothing is restored yet --
    // restoring now would compare against a fingerprint that is only absent.
    await waitFor(() => expect(screen.getByTestId("edit-panel")).toBeTruthy());
    expect(screen.queryByTestId("draft-restored")).toBeNull();

    srv.releaseCast();
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/The bundle has been recompiled since this draft was taken/);

    // ...and the draft is NOT re-stamped with the new build: the evidence
    // survives for the NEXT restore too.
    //
    // Read ONCE, after the effects have drained, and sampled again later --
    // never through `waitFor`, which asserts a value BECAME what was expected
    // and so passed on a poll that happened to catch the pre-stamp value
    // (found by running this test alone: it failed alone and passed in the
    // file, which is a scheduling coincidence, not a result).
    await drained();
    expect(storedFingerprint()).toBe("fp-OLD");
    await delay(30);
    expect(storedFingerprint()).toBe("fp-OLD");

    // And a genuine SAVE does advance it -- the one event that means "what is
    // staged next was staged against the build on screen".
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    await drained();
    // Nothing pending now, so the key is cleared; stage something and the new
    // draft carries the current build.
    const adds = await screen.findAllByTestId("outline-site-add");
    fireEvent.click(adds.find((b) => b.getAttribute("data-site") === "site:10.20")!);
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "after the save" } });
    fireEvent.submit(form);
    await waitFor(() => expect(storedFingerprint()).toBe("fp-NEW"));
  });

  it("the warning is not ONE-SHOT: a second reload still reports the recompile", async () => {
    // The stamp used to advance on the first `edits.etag` the store saw -- the
    // INITIAL LOAD's own -- so the warning fired once and was silent ever after.
    //
    // On the SLOW-cast ordering, which is where that happened and the one M3 is
    // about: a fresh server per open, because a held gate resolves only once
    // and a second open against the same one would take the fast path -- where
    // the stamp does NOT move, so the test would pass with the bug in place.
    seedDraft("10.80.90.0/24", 1, { fingerprint: "fp-OLD" });
    for (const pass of [1, 2]) {
      const srv = server({ holdCast: true, fingerprint: "fp-NEW" });
      open(`/design/${IMPRINT}`, srv.api);
      await screen.findByTestId("edit-panel");
      srv.releaseCast();
      const notice = await screen.findByTestId("draft-restored");
      expect(notice.textContent, `pass ${pass}`).toMatch(/has been recompiled/);
      await drained();
      expect(storedFingerprint(), `pass ${pass}`).toBe("fp-OLD");
    }
  });

  it("says NOTHING when the bundle's content fingerprint is unchanged", async () => {
    seedDraft("10.80.90.0/24", 1, { fingerprint: "fp-SAME" });
    open(`/design/${IMPRINT}`, server({ fingerprint: "fp-SAME" }).api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).not.toMatch(/recompiled/);
  });

  it("a draft kept by an EARLIER Console (no bundle stamp) is reported as unknown, not unchanged", async () => {
    // What an earlier Console wrote: the PIT build stamp under `fingerprint`,
    // equal to what the cast still publishes -- a check keyed on it would say
    // nothing, which reads as "unchanged" (LES-273/LES-333).
    window.localStorage.setItem(
      draftKey(IMPRINT),
      JSON.stringify({
        schema: DRAFT_SCHEMA,
        imprint: IMPRINT,
        saved_at: "2026-09-16T10:00:00Z",
        etag: "etag-1",
        fingerprint: "pit-stamp-constant",
        entries: [{ block: "add", record: { kind: "segment", key: "10.80.90.0/24", name: "lab", note: "mine" } }],
      }),
    );
    open(`/design/${IMPRINT}`, server().api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/^Restored 1 unsaved edit/);
    expect(notice.textContent).toMatch(
      /could not be checked — the draft was kept by an earlier Console that did not record which bundle it was taken against/,
    );
  });

  it("says the check could not be MADE when the cast read failed", async () => {
    seedDraft("10.80.90.0/24", 1, { fingerprint: "fp-OLD" });
    open(`/design/${IMPRINT}`, server({ castFails: true }).api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/could not be checked/);
  });
});

describe("discarding a restored draft drops its pins too (S1)", () => {
  it("leaves no pin naming a segment no record adds", async () => {
    const srv = server();
    const first = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(first.container);
    await waitFor(() => expect(srv.layoutPuts).toHaveLength(1));
    expect(Object.keys(srv.pins())).toEqual(["segment:10.80.0.0/24"]);

    open(`/design/${IMPRINT}`, srv.api);
    await screen.findByTestId("draft-restored");
    fireEvent.click(screen.getByTestId("draft-discard"));
    await waitFor(() => expect(screen.getByTestId("change-list-empty")).toBeTruthy());
    // The SAME rule the change list's remove applies -- one function, both
    // callers -- so the discard cannot leave iteration 8's orphan behind.
    await waitFor(() => expect(srv.pins()).toEqual({}));
  });
});

describe("the leave guard DISMISSES, it never acts (S4)", () => {
  it("undoing the last gesture under the prompt clears it and stays put", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.click(within(screen.getByLabelText("Breadcrumb")).getByText("imprints"));
    expect(await screen.findByTestId("leave-guard")).toBeTruthy();

    // Answer the guard's REASON rather than the guard: undo the edits.
    fireEvent.click(within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo"));
    await waitFor(() => expect(screen.queryByTestId("leave-guard")).toBeNull());
    // Still on the imprint: the operator never said "leave".
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
    expect(screen.getByTestId("change-list-empty")).toBeTruthy();
  });
});

describe("a removal that cannot re-save is DISABLED, with the reason (S5)", () => {
  it("does not offer an undo that would silently do nothing", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByTestId("change-gesture-row").getAttribute("data-state")).toBe("saved"),
    );

    // A source the save path refuses. The undo now re-saves the file, so it
    // needs everything Save needs -- and used to stay enabled and do nothing.
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "not-an-operator" } });
    const undo = within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo") as HTMLButtonElement;
    await waitFor(() => expect(undo.disabled).toBe(true));
    expect(screen.getAllByTestId("remove-blocked")[0]!.textContent).toMatch(
      /this removal re-saves the file, which needs a valid source and as_of below/,
    );
    const before = srv.assertionPuts.length;
    fireEvent.click(undo);
    expect(srv.assertionPuts).toHaveLength(before);

    // Put the source back and it works again.
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "operator: tester" } });
    await waitFor(() => expect(undo.disabled).toBe(false));
    fireEvent.click(undo);
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(before + 1));
  });
});

describe("leaving an imprint by the browser's own navigation is SAID at the destination (S9)", () => {
  it("names the imprint, its count and the way back", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    // *Leave anyway* is the in-app equivalent of a Back the guard cannot
    // intercept: the destination says whose unsaved work was left behind.
    fireEvent.click(within(screen.getByLabelText("Breadcrumb")).getByText("imprints"));
    fireEvent.click(await screen.findByTestId("leave-anyway"));
    const notice = await screen.findByTestId("left-behind");
    expect(notice.textContent).toMatch(/18 unsaved edits in trimmed-fixture are kept as a draft on this browser/);
    fireEvent.click(screen.getByTestId("left-behind-back"));
    await waitFor(() => expect(screen.queryByTestId("left-behind")).toBeNull());
    // ...and the work is there.
    await waitFor(() => expect(screen.getAllByTestId("change-gesture-row")).toHaveLength(1));
  });
});

describe("the restore notice's count is the store's own delta (N2)", () => {
  it("what the notice claims is what the store actually gained", async () => {
    // The notice is built from `reconcileDraft` while the reducer applies its
    // own held-id filter; two filters, one number, and nothing checked they
    // agreed (LES-330's shape).
    const srv = server();
    const first = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(first.container);

    open(`/design/${IMPRINT}`, srv.api);
    const notice = await screen.findByTestId("draft-restored");
    const claimed = Number(/Restored (\d+) unsaved edit/.exec(notice.textContent ?? "")![1]);
    expandGestures();
    const rows = screen.getAllByTestId("change-row");
    expect(rows).toHaveLength(claimed);
    for (const r of rows) expect(r.getAttribute("data-state")).toBe("pending");
  });
});

// ===========================================================================
// the final fix round -- B, D, E
// ===========================================================================
describe("undo of a PENDING-only gesture needs no save metadata (fix round B)", () => {
  it("works with an empty source, because nothing re-saves the file", async () => {
    // The S5 fix gated every removal on the save metadata. Nothing re-saves
    // here -- no record of the gesture was ever in the file -- so the control
    // was disabled under a reason that was untrue.
    const srv = server({ file: fileDoc(IMPRINT, { source: "" }) });
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "" } });

    const row = screen.getByTestId("change-gesture-row");
    const undo = within(row).getByTestId("gesture-undo") as HTMLButtonElement;
    await waitFor(() => expect(undo.disabled).toBe(false));
    expect(screen.queryByTestId("remove-blocked")).toBeNull();

    fireEvent.click(undo);
    await waitFor(() => expect(screen.getByTestId("change-list-empty")).toBeTruthy());
    // ...and no PUT was attempted: a pending-only removal is a discard.
    expect(srv.assertionPuts).toHaveLength(0);
  });

  it("a per-record remove inside a pending gesture is enabled too", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "" } });
    expandGestures();
    const rows = screen.getAllByTestId("change-row");
    const host = rows.find((r) => r.textContent?.includes("+ host"))!;
    const remove = within(host).getByTestId("change-remove") as HTMLButtonElement;
    await waitFor(() => expect(remove.disabled).toBe(false));
    fireEvent.click(remove);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(17));
    expect(srv.assertionPuts).toHaveLength(0);
  });

  it("but a gesture holding a SAVED record still needs it, and says so", async () => {
    const srv = server();
    const { container } = open(`/design/${IMPRINT}`, srv.api);
    await stageClone(container);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(srv.assertionPuts).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByTestId("change-gesture-row").getAttribute("data-state")).toBe("saved"),
    );
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "not-an-operator" } });
    const undo = within(screen.getByTestId("change-gesture-row")).getByTestId("gesture-undo") as HTMLButtonElement;
    await waitFor(() => expect(undo.disabled).toBe(true));
    expect(screen.getAllByTestId("remove-blocked")[0]!.textContent).toMatch(/this removal re-saves the file/);
  });
});

describe("a WORLD-dependent refusal keeps the draft (fix round D)", () => {
  it("keeps it when the file holds a conflicting record, and the notice offers the discard", async () => {
    const suppressed = { kind: "host", key: "address:10.80.90.10", note: "out of scope" };
    window.localStorage.setItem(
      draftKey(IMPRINT),
      JSON.stringify({
        schema: DRAFT_SCHEMA,
        imprint: IMPRINT,
        saved_at: "2026-09-16T10:00:00Z",
        etag: "etag-1",
        bundle_fingerprint: "fp-1",
        entries: [{ block: "add", record: { kind: "host", key: "address:10.80.90.10", segment: "10.80.20.0/24", note: "mine" } }],
      }),
    );
    const srv = server({ file: fileDoc(IMPRINT, { suppress: [suppressed] }) });
    open(`/design/${IMPRINT}`, srv.api);
    const notice = await screen.findByTestId("draft-restored");
    expect(notice.textContent).toMatch(/is already in `suppress`/);
    await drained();
    // KEPT: the operator can lift the suppression and want their copy back.
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();
    await delay(30);
    expect(window.localStorage.getItem(draftKey(IMPRINT))).not.toBeNull();

    // ...and it CAN be discarded. The control used to be gated on there being
    // restored edits, so a kept draft -- which by definition restored nothing --
    // left the operator with the notice and no way to clear the key.
    // Nothing of the DRAFT was restored -- the file's own suppression is the
    // only row, and there is no pending add of the key it refused.
    const rows = screen.getAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute("data-block")).toBe("suppress");
    expect(rows[0]!.getAttribute("data-state")).toBe("saved");
    const discard = screen.getByTestId("draft-discard");
    expect(discard.textContent).toBe("discard the draft");
    fireEvent.click(discard);
    await waitFor(() => expect(window.localStorage.getItem(draftKey(IMPRINT))).toBeNull());
    expect(screen.queryByTestId("draft-restored")).toBeNull();
  });

  it("drops one refused for a reason that can never change", async () => {
    window.localStorage.setItem(
      draftKey(IMPRINT),
      JSON.stringify({
        schema: DRAFT_SCHEMA,
        imprint: IMPRINT,
        saved_at: "2026-09-16T10:00:00Z",
        etag: "etag-1",
        bundle_fingerprint: "fp-1",
        entries: [{ block: "add", record: { kind: "wormhole", key: "x", note: "n" } }],
      }),
    );
    open(`/design/${IMPRINT}`, server().api);
    expect((await screen.findByTestId("draft-restored")).textContent).toMatch(/an `add` record's kind is one of/);
    await drained();
    expect(window.localStorage.getItem(draftKey(IMPRINT))).toBeNull();
  });
});

describe("the CATALOG says which imprints hold drafts (fix round E)", () => {
  it("names them on a cold start, with a way into each", async () => {
    // A new tab: nothing has reported a count, so the destination notice
    // cannot fire -- and the drafts are still sitting here.
    seedDraft("10.80.90.0/24", 2);
    open("/imprints", server().api);
    const here = await screen.findByTestId("drafts-here");
    expect(here.textContent).toMatch(/1 imprint on this browser has unsaved drafts/);
    expect(here.textContent).toMatch(/3 records/);
    const link = screen.getByTestId("draft-here");
    expect(link.getAttribute("data-imprint")).toBe(IMPRINT);
    fireEvent.click(link);
    await waitFor(() => expect(screen.queryByTestId("drafts-here")).toBeNull());
    expect(await screen.findByTestId("draft-restored")).toBeTruthy();
  });

  it("says nothing when this browser holds none", async () => {
    open("/imprints", server().api);
    await screen.findByLabelText("Imprints");
    expect(screen.queryByTestId("drafts-here")).toBeNull();
  });
});
