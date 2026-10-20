/**
 * S5 editing + save and S5.1.a's selection-driven interaction, wired through
 * the real shell (`TestApp` -> `ConsoleShell` -> `ImprintWorkspace` ->
 * `Inspector` -> `ElementActions` / `EditPanel`) against a fake API. The pure
 * gesture -> record proof is `records.test.ts`; the reducer's three states are
 * `store.test.ts`; the pure selection/allocation rules are `selection.test.ts`.
 * This file proves the WIRING.
 *
 * **S5.1.a restated the PATHS, not the assertions** (LES-299). Every S5 test
 * below asserts exactly what it asserted before -- the record that reaches the
 * PUT, the change-list tag, the 412 reload rule, the remove semantics -- but
 * reaches its form the way an operator now does: a gesture is an ACTION on the
 * selected element, and "add a segment" is a `+` on a site. The old free-standing
 * forms are gone, and the test that they are gone is part of the acceptance.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type { AssertionsDoc, CastSummary, ImprintList, ImprintProvenance, TopologyExport } from "../src/api/types";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { initialEditState } from "../src/edit/store";
import { buildGraph } from "../src/model/graph";
import { ElementActions } from "../src/views/ElementActions";
import type { Selection } from "../src/views/selection";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const IMPRINT = "trimmed-fixture";

const CAST: CastSummary = {
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
  segments: trimmedExport.segments.length,
  node_specs: 40,
  vms: 40,
  hosts: 40,
  conduits: 0,
  conduit_roster: [],
  routers: [],
  observed_nodes: trimmedExport.nodes.length,
  observed_edges: trimmedExport.edges.length,
  observed_segments: trimmedExport.segments.length,
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

/** An EXISTING assertions file, so `source`/`as_of` seed valid and Save is
 *  enabled without the operator retyping them for every test. */
function existingDoc(overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/${IMPRINT}.assertions.yaml`,
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: IMPRINT,
    as_of: "2026-09-08T00:00:00Z",
    source: "operator: tester",
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

interface FakeOpts {
  assertions?: AssertionsDoc;
  putAssertions?: ConsoleApi["putAssertions"];
  puts?: Array<{ id: string; document: Record<string, unknown>; etag: string | null }>;
  /** A different topology export -- the over-budget fixture, for the collapsed
   *  segment case (R6 gives a group of more than six segments a `+N more`). */
  exportData?: TopologyExport;
  /** Fields layered over the cast summary -- `segment_roster` (A1), so a test
   *  exercises the roster through the seam the live app reads it by. */
  cast?: Partial<CastSummary>;
  /** Replace the topology read (a 404, a failure). */
  getTopology?: ConsoleApi["getTopology"];
}

function fakeApi(opts: FakeOpts = {}): ConsoleApi {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const doc = opts.assertions ?? existingDoc();
  const puts = opts.puts;
  const exportData = opts.exportData ?? (trimmedExport as TopologyExport);
  const defaultPut: ConsoleApi["putAssertions"] = (id, document, etag) => {
    puts?.push({ id, document, etag });
    // Echo the PUT's blocks into the returned doc, so the store flips the
    // named pending entries to `saved` (a real server writes and returns them).
    const d = document as Record<string, unknown>;
    return Promise.resolve(
      existingDoc({
        imprint_id: id,
        imprint: id,
        etag: "etag-2",
        add: (d["add"] as AssertionsDoc["add"]) ?? [],
        override: (d["override"] as AssertionsDoc["override"]) ?? [],
        suppress: (d["suppress"] as AssertionsDoc["suppress"]) ?? [],
      }),
    );
  };
  return {
    listImprints: () =>
      Promise.resolve({
        imprints: [{ id: IMPRINT, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" }],
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROVENANCE),
    getTopology: opts.getTopology ?? (() => Promise.resolve(exportData)),
    getCast: (id) => Promise.resolve({ ...CAST, ...opts.cast, id, twin_config_id: id }),
    getAssertions: () => Promise.resolve(doc),
    putAssertions: opts.putAssertions ?? defaultPut,
    // `plane: null` -- no saved layout, so no plane stamp either (R2).
    getLayout: () => Promise.resolve({ imprint_id: IMPRINT, path: "", exists: false, etag: null, schema: "axr-layout/1", imprint: IMPRINT, pins: {}, plane: null }),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
}

/** Open a site's `+` from the OUTLINE (S5.1.a) and return the add-segment form.
 *  The map's site header carries the same gesture -- proven separately below. */
async function openSiteAdd(site: string): Promise<HTMLElement> {
  const adds = await screen.findAllByTestId("outline-site-add");
  const button = adds.find((b) => b.getAttribute("data-site") === site);
  if (!button) throw new Error(`no site + for ${site}`);
  fireEvent.click(button);
  return screen.findByTestId("form-add-segment");
}

/** Open one of the selected element's actions and return its form. */
async function openAction(id: string, formTestId: string): Promise<HTMLElement> {
  fireEvent.click(await screen.findByTestId(`action-open-${id}`));
  return screen.findByTestId(formTestId);
}

// ---------------------------------------------------------------------------
// S5.1.a -- selection drives editing
// ---------------------------------------------------------------------------

/** The router's CURRENT location, so a test asserts the URL a click produced. */
function LocationProbe(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="loc">{`${loc.pathname}${loc.search}`}</output>;
}

function renderWithLocation(url: string, opts: FakeOpts = {}): ReturnType<typeof render> {
  return render(
    <TestApp client={fakeApi(opts)} url={url}>
      <>
        <ConsoleRoutes />
        <LocationProbe />
      </>
    </TestApp>,
  );
}

/** A tile's `✎` -- the `Nav` group, which is what carries the role, the tab
 *  stop and the handlers (the rect inside it carries the test id). */
function tileSelect(container: HTMLElement, cidr: string): HTMLElement {
  const rect = container.querySelector(`[data-testid="tile-actions"][data-cidr="${cidr}"]`);
  if (!rect?.parentElement) throw new Error(`no tile ✎ for ${cidr}`);
  return rect.parentElement;
}

/** A site header's `+` on the MAP -- again the `Nav` group around the rect. */
function mapSiteAdd(container: HTMLElement, site: string): HTMLElement {
  const rect = container.querySelector(`[data-testid="site-add"][data-site="${site}"]`);
  if (!rect?.parentElement) throw new Error(`no map site + for ${site}`);
  return rect.parentElement;
}

const SEGMENT_ACTIONS = ["add-host", "clone", "add-conduit", "suppress-hosts"] as const;
const HOST_ACTIONS = ["change-role", "override", "suppress"] as const;

function selectionKind(): string | null {
  return screen.getByTestId("element-actions").getAttribute("data-selection");
}

function cidrField(form: HTMLElement): HTMLInputElement {
  return within(form).getByTestId("seg-cidr") as HTMLInputElement;
}

/** The add-segment form as it is on screen NOW. */
function liveSegmentForm(): HTMLElement {
  return screen.getByTestId("form-add-segment");
}

describe("S5.1.a: the free-standing forms are GONE", () => {
  it("offers no gesture form at the network level until something is selected", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    // The S5 panel used to render add-segment and add-conduit here, keyed on
    // the zoom LEVEL alone and belonging to nothing on the map.
    expect(screen.queryByTestId("form-add-segment")).toBeNull();
    expect(screen.queryByTestId("form-add-conduit")).toBeNull();
    // ...and it says how to get one, rather than showing an empty pane.
    expect(screen.getByTestId("no-selection")).toBeTruthy();
  });

  it("every gesture form is inside the SELECTED element's actions, never in the change panel", async () => {
    // Each subject with each of its actions opened in turn (the list is an
    // accordion), so a form still living in `EditPanel` would be on screen to
    // be found in the wrong place.
    const subjects: Array<[string, readonly string[]]> = [
      [`/design/${IMPRINT}/segments/10.80.20.0-24`, ["add-host", "add-conduit", "suppress-hosts"]],
      [`/design/${IMPRINT}/hosts/hostname:axdc01`, HOST_ACTIONS],
    ];
    for (const [url, actions] of subjects) {
      const { unmount } = render(<TestApp client={fakeApi()} url={url} />);
      for (const id of actions) {
        fireEvent.click(await screen.findByTestId(`action-open-${id}`));
        const forms = screen.getAllByTestId(/^form-/);
        expect(forms.length, `${url} ${id}`).toBeGreaterThan(0);
        for (const form of forms) {
          expect(screen.getByTestId("element-actions").contains(form), `${url} ${id}`).toBe(true);
          expect(screen.getByTestId("edit-panel").contains(form), `${url} ${id}`).toBe(false);
        }
      }
      unmount();
    }
  });
});

describe("S5.1.a: selecting a tile shows THAT SEGMENT's facts and actions, in place", () => {
  it("keeps level 0 on screen and offers the segment's facts and its four actions", async () => {
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    fireEvent.click(tileSelect(container, "10.80.20.0/24"));

    // The inspector now names the segment...
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.20.0/24");
    // ...shows its FACTS -- the export's own name and role mix, not a label...
    const facts = screen.getByTestId("segment-facts");
    expect(facts.textContent).toContain("seg-10-80-20-0");
    expect(facts.textContent).toContain("domain-controller 1");
    expect(facts.textContent).toContain("dns-resolver 1");
    // ...and offers its actions,
    expect(screen.getByTestId("segment-actions")).toBeTruthy();
    for (const id of SEGMENT_ACTIONS) {
      expect(screen.getByTestId(`action-open-${id}`)).toBeTruthy();
    }
    // WITHOUT drilling: selecting is not opening, so the overview stays.
    expect(screen.getByTestId("level0-svg")).toBeTruthy();
    expect(screen.queryByTestId("level1-svg")).toBeNull();
  });

  it("states WHY clone is unavailable instead of hiding it -- on screen AND as the control's description", async () => {
    // Restated for S5.1.b (LES-299). S5.1.a showed *Clone this segment…* as
    // unavailable on EVERY segment, because cloning was not built. It is now:
    // on an OBSERVED segment the control is an ordinary action that starts the
    // clone. The property this test pins -- an unavailable action says WHY, on
    // screen and at the control, and stays in the tab order -- moves to the
    // segment that still cannot be cloned: one this FILE adds, which has no
    // placed hosts to copy.
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    fireEvent.click(tileSelect(container, "10.80.20.0/24"));

    // An observed segment: available -- no reason to dangle, and pressing it
    // opens the clone (its placement step), not a form under the button.
    const observedClone = (await screen.findByTestId("action-open-clone")) as HTMLButtonElement;
    expect(observedClone.hasAttribute("aria-disabled")).toBe(false);
    expect(observedClone.hasAttribute("aria-describedby")).toBe(false);
    expect(screen.queryByTestId("action-why-clone")).toBeNull();

    // An ADDED segment: stage one from its site's `+`; A3 selects it.
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.0.0/24"));

    const clone = (await screen.findByTestId("action-open-clone")) as HTMLButtonElement;
    // `aria-disabled`, not `disabled` -- the control stays in the TAB ORDER so a
    // keyboard user reaches it and its reason -- and pressing it opens nothing.
    expect(clone.getAttribute("aria-disabled")).toBe("true");
    expect(clone.disabled).toBe(false);
    fireEvent.click(clone);
    expect(clone.getAttribute("aria-expanded")).toBe("false");
    expect(selectionKind()).toBe("segment");
    // The reason is ON SCREEN -- an unavailable control with no reason is
    // indistinguishable from a broken one -- in plain language, and naming a
    // path that EXISTS.
    const why = screen.getByTestId("action-why-clone");
    expect(why.textContent).toMatch(/^A clone copies an observed segment's placed hosts/);
    expect(why.textContent).toMatch(/Add host here/);
    expect(why.textContent).not.toMatch(/slice/);
    // ...and it is the control's accessible description, so a screen reader
    // hears it AT the control rather than as a stray paragraph after it.
    expect(why.id).not.toBe("");
    expect(clone.getAttribute("aria-describedby")).toBe(why.id);
    // The AVAILABLE actions carry no description to dangle.
    expect(screen.getByTestId("action-open-add-host").hasAttribute("aria-describedby")).toBe(false);
  });
});

describe("S5.1.a: at level 1 the SPINE is the selectable subject, and a CHIP drills to its host", () => {
  it("the spine brings the segment back after the outline's site + took the one selection slot", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    // Arriving IS selecting: the URL names the segment.
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    await openSiteAdd("site:10.20");
    expect(selectionKind()).toBe("site-add");

    fireEvent.click(screen.getByTestId("spine").parentElement!);
    expect(selectionKind()).toBe("segment");
    expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.20.0/24");
    expect(screen.getByTestId("segment-facts").textContent).toContain("seg-10-80-20-0");
    for (const id of SEGMENT_ACTIONS) {
      expect(screen.getByTestId(`action-open-${id}`)).toBeTruthy();
    }
    expect(screen.queryByTestId("form-add-segment")).toBeNull();
  });

  it("a chip DRILLS to its host's route, and the host that route names is the selection -- facts and three actions", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    fireEvent.click(await screen.findByLabelText("domain-controller: hostname:axdc01"));

    await waitFor(() => expect(selectionKind()).toBe("host"));
    expect(screen.getByTestId("selection-title").textContent).toBe("Host hostname:axdc01");
    const facts = screen.getByTestId("host-facts");
    expect(facts.textContent).toContain("10.80.20.10");
    expect(facts.textContent).toContain("10.80.20.0/24");
    expect(facts.textContent).toContain("domain-controller");
    for (const id of HOST_ACTIONS) {
      expect(screen.getByTestId(`action-open-${id}`)).toBeTruthy();
    }
    // A host is not a segment: none of the segment's actions ride along.
    expect(screen.queryByTestId("segment-actions")).toBeNull();
  });
});

describe("S5.1.a: opening an element selects it -- which is how a COLLAPSED segment is reached", () => {
  it("a segment with no tile (R6 budget) still gets the same facts and actions, from the outline", async () => {
    const over = withOverBudgetSite();
    const { container } = render(
      <TestApp client={fakeApi({ exportData: over })} url={`/design/${IMPRINT}`} />,
    );
    await screen.findByTestId("level0-svg");

    // Find a segment the MAP does not draw but the OUTLINE lists -- the
    // collapsed set R6's `+N more` stands for.
    const drawn = new Set(
      [...container.querySelectorAll("[data-pinnable]")]
        .map((n) => n.getAttribute("data-pinnable") ?? "")
        .filter((k) => k.startsWith("segment:"))
        .map((k) => k.slice("segment:".length)),
    );
    const collapsed = over.segments.map((s) => s.cidr).filter((c) => !drawn.has(c));
    expect(collapsed.length).toBeGreaterThan(0);
    const cidr = collapsed[0]!;
    // No tile, so no `✎` to select it in place.
    expect(container.querySelector(`[data-testid="tile-actions"][data-cidr="${cidr}"]`)).toBeNull();

    // The outline carries every segment regardless of the map's budget. The
    // query is scoped TO THE OUTLINE: the map's `+N more` tile names every
    // segment it hides in its own accessible name (LES-307), so an unscoped
    // "a button named <cidr>" finds that tile too -- and the path under test is
    // the outline row. (That the tile names it is pinned here, so the scoping
    // stays explained rather than looking arbitrary.)
    expect(screen.getByTestId("overflow-tile").parentElement!.getAttribute("aria-label")).toContain(cidr);
    const outline = screen.getByRole("navigation", { name: "Imprints" });
    fireEvent.click(within(outline).getByRole("button", { name: new RegExp(cidr.replace(/[.\\/]/g, "\\$&")) }));

    // Opening it selected it, with its facts and the SAME four actions.
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    expect(screen.getByTestId("selection-title").textContent).toBe(`Segment ${cidr}`);
    expect(screen.getByTestId("segment-facts").textContent).toContain(`seg-${cidr.split("/")[0]!.replace(/\./g, "-")}`);
    for (const id of SEGMENT_ACTIONS) {
      expect(screen.getByTestId(`action-open-${id}`)).toBeTruthy();
    }
  });
});

describe("S5.1.a: a host carries its own three actions", () => {
  it("shows the host's facts and change-role / override / suppress for the open host", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("host");
    expect(screen.getByTestId("host-facts").textContent).toContain("10.80.20.10");
    expect(screen.getByTestId("host-actions")).toBeTruthy();
    for (const id of HOST_ACTIONS) {
      expect(screen.getByTestId(`action-open-${id}`)).toBeTruthy();
    }
    // The segment's own actions are NOT offered on a host.
    expect(screen.queryByTestId("segment-actions")).toBeNull();
  });

  it("*Override image / resources* stages its two `override` records, one per form", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    fireEvent.click(await screen.findByTestId("action-open-override"));

    const image = await screen.findByTestId("form-override-image");
    fireEvent.change(within(image).getByTestId("image-value"), { target: { value: "debian-12" } });
    fireEvent.change(within(image).getByTestId("gesture-note"), { target: { value: "pin the image" } });
    fireEvent.submit(image);

    const sizing = screen.getByTestId("form-override-resources");
    fireEvent.change(within(sizing).getByTestId("res-cpu"), { target: { value: "2" } });
    fireEvent.change(within(sizing).getByTestId("res-ram"), { target: { value: "4096" } });
    fireEvent.change(within(sizing).getByTestId("gesture-note"), { target: { value: "size it up" } });
    fireEvent.submit(sizing);

    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(2));
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.document["override"]).toEqual([
      { key: "hostname:axdc01", field: "image", value: "debian-12", note: "pin the image" },
      { key: "hostname:axdc01", field: "resources", value: { cpu: 2, ram: 4096 }, note: "size it up" },
    ]);
  });
});

describe("S5.1.a: the site `+` prefills the next free /24 and refuses a taken one", () => {
  it("prefills the next free /24 in that site's two-octet prefix", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    // The fixture's `10.80` holds .20 and .72, so .0 is the first free one.
    expect(cidrField(form).value).toBe("10.80.0.0/24");
    // ...and the name prefills from it, so the gesture is "+ , note, Stage".
    expect((within(form).getByTestId("seg-name") as HTMLInputElement).value).toBe("seg-10-80-0-0");
    expect(screen.getByTestId("site-add-suggestion").textContent).toContain("10.80.0.0/24");
  });

  it("with NO roster published, REFUSES an observed CIDR -- said while typing, refused on Stage, nothing staged", async () => {
    // Restated for A2 (LES-299). This fixture's cast summary publishes no
    // segment roster (a server predating it), so whether the cast kept or
    // pruned 10.80.20.0/24 cannot be told: the refusal is conservative, and it
    // says WHY instead of claiming the twin has the segment.
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(cidrField(form), { target: { value: "10.80.20.0/24" } });
    const note = within(form).getByTestId("seg-claim").textContent ?? "";
    expect(note).toMatch(/^10\.80\.20\.0\/24 is observed in this twin's export, but this server does not publish the cast's segment roster/);
    expect(note).toMatch(/\. Stage refuses it\.$/);
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);

    expect(within(form).getByTestId("gesture-error").textContent).toMatch(
      /^10\.80\.20\.0\/24 is observed in this twin's export/,
    );
    expect(screen.queryByTestId("change-row")).toBeNull();
  });

  it("refuses a CIDR a PENDING add already claimed, not just one the twin has", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(form); // stages 10.80.0.0/24

    await screen.findByTestId("change-row");
    // Re-open the `+`: the suggestion has MOVED PAST the staged one.
    const again = await openSiteAdd("site:10.80");
    expect(cidrField(again).value).toBe("10.80.1.0/24");
    expect(screen.getByTestId("site-add-suggestion").textContent).toContain("10.80.1.0/24");
    // ...and re-typing the staged CIDR is refused, naming the PENDING add as
    // the claim rather than blaming the twin.
    fireEvent.change(cidrField(again), { target: { value: "10.80.0.0/24" } });
    fireEvent.change(within(again).getByTestId("gesture-note"), { target: { value: "again" } });
    fireEvent.submit(again);
    expect(within(again).getByTestId("gesture-error").textContent).toMatch(
      /^10\.80\.0\.0\/24 is already claimed by a pending add/,
    );
    expect(screen.getAllByTestId("change-row")).toHaveLength(1);
    // The refusal is what kept the FIRST add: the store keys an add by its key,
    // so a staged duplicate would have silently REPLACED it. Read off the file.
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    // No roster declares a zone here, so the add carries the twin config's own
    // default, `simple` (restated for the zone fallback, LES-299).
    expect(puts[0]!.document["add"]).toEqual([
      { kind: "segment", key: "10.80.0.0/24", name: "seg-10-80-0-0", zone_type: "simple", note: "the OT lab" },
    ]);
  });

  it("a SAVED add in the file is as taken as a pending one", async () => {
    const assertions = existingDoc({
      add: [{ kind: "segment", key: "10.80.0.0/24", name: "seg-10-80-0-0", note: "from the file" }],
    });
    render(<TestApp client={fakeApi({ assertions })} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    await waitFor(() => expect(cidrField(form).value).toBe("10.80.1.0/24"));
    fireEvent.change(cidrField(form), { target: { value: "10.80.0.0/24" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    expect(within(form).getByTestId("gesture-error").textContent).toMatch(/claimed by a saved add/);
  });

  it("an UNTOUCHED field follows the suggestion both ways -- past a staged add, and back on its removal", async () => {
    // Restated for A3 (LES-299): a successful stage now SELECTS the new
    // segment, so the form gives way to its frame and the `+` is pressed again
    // to see the suggestion move on. (Read off the LIVE form each time -- a held
    // node would go stale on a remount and report its last value.)
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(form); // stages 10.80.0.0/24
    await screen.findByTestId("change-row");
    expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.0.0/24");

    await openSiteAdd("site:10.80");
    expect(cidrField(liveSegmentForm()).value).toBe("10.80.1.0/24");
    expect((within(liveSegmentForm()).getByTestId("seg-name") as HTMLInputElement).value).toBe("seg-10-80-1-0");

    // Discarding the pending add frees 10.80.0.0/24 -- and the field follows it back.
    fireEvent.click(screen.getByTestId("change-remove"));
    await waitFor(() => expect(screen.queryByTestId("change-row")).toBeNull());
    expect(cidrField(liveSegmentForm()).value).toBe("10.80.0.0/24");
  });

  it("what the operator TYPED is never overwritten by a changed suggestion", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "first" } });
    fireEvent.submit(form); // stages 10.80.0.0/24 -- and selects it (A3)
    await screen.findByTestId("change-row");
    await openSiteAdd("site:10.80");

    // Mid-edit: a CIDR and a note typed into the live form, not staged.
    fireEvent.change(cidrField(liveSegmentForm()), { target: { value: "10.80.40.0/24" } });
    fireEvent.change(within(liveSegmentForm()).getByTestId("gesture-note"), { target: { value: "mid-edit" } });
    expect(cidrField(liveSegmentForm()).value).toBe("10.80.40.0/24");
    // Something ELSE moves the suggestion: the pending add is discarded.
    fireEvent.click(screen.getByTestId("change-remove"));
    await waitFor(() =>
      expect(screen.getByTestId("site-add-suggestion").textContent).toContain("10.80.0.0/24"),
    );
    // The typed values stand; the untouched name still tracks the typed CIDR.
    expect(cidrField(liveSegmentForm()).value).toBe("10.80.40.0/24");
    expect((within(liveSegmentForm()).getByTestId("seg-name") as HTMLInputElement).value).toBe("seg-10-80-40-0");
    expect((within(liveSegmentForm()).getByTestId("gesture-note") as HTMLInputElement).value).toBe("mid-edit");

    // Staging what was typed stages exactly THAT, and selects it (A3).
    fireEvent.submit(liveSegmentForm());
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.40.0/24");
  });

  it("an OVERLAP is said, not refused -- naming the check that exists downstream (NET-010) and when it does not apply", async () => {
    // Restated for B9 (LES-299): the note names the real downstream check.
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    fireEvent.change(cidrField(form), { target: { value: "10.80.20.128/25" } });
    expect(within(form).getByTestId("seg-claim").textContent).toBe(
      "10.80.20.128/25 overlaps 10.80.20.0/24, an observed segment — the writer accepts it; axRange's validator " +
        "refuses overlapping networks (NET-010) when a cast is not relocated, while a relocated (--relocate) cast " +
        "gives each segment its own /24.",
    );
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the real subnetting" } });
    fireEvent.submit(form);
    expect(await screen.findAllByTestId("change-row")).toHaveLength(1);
    expect(screen.queryByTestId("gesture-error")).toBeNull();
  });

  it("a site-LESS add from the outline root lands in misc, and says so", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    fireEvent.click(await screen.findByTestId("outline-add-segment"));
    const form = await screen.findByTestId("form-add-segment");
    // No prefix to allocate in, so nothing is prefilled -- and it says why, and
    // where such a segment goes, BEFORE anything is typed. (B6: "would sit",
    // never a promised map placement.)
    expect(cidrField(form).value).toBe("");
    expect(screen.getByTestId("site-add-noprefix").textContent).toMatch(/would sit in the shared misc group/);

    fireEvent.change(cidrField(form), { target: { value: "10.99.1.0/24" } });
    // A prefix no other segment shares folds into the shared `misc` group.
    expect(screen.getByTestId("seg-landing").textContent).toMatch(/misc/);

    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(form);
    const rows = await screen.findAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(within(rows[0]!).getByTestId("change-tag").textContent).toBe("te deploy --update");
  });

  it("the MAP's site header carries the same `+` as the outline's site row", async () => {
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    fireEvent.click(mapSiteAdd(container, "site:10.20"));
    const form = await screen.findByTestId("form-add-segment");
    expect(cidrField(form).value).toBe("10.20.0.0/24");
  });
});

describe("S5.1.a: every new affordance is reachable by keyboard", () => {
  it("a tile's select control is a tab stop; Enter selects the segment and moves focus to what it opened", async () => {
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    const pencil = tileSelect(container, "10.80.20.0/24");
    expect(pencil.getAttribute("role")).toBe("button");
    expect(pencil.getAttribute("tabindex")).toBe("0");
    expect(pencil.getAttribute("aria-label")).toMatch(/^Select segment 10\.80\.20\.0\/24/);

    pencil.focus();
    fireEvent.keyDown(pencil, { key: "Enter" });
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    // Without the move, the actions sit after every remaining tab stop of the map.
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
  });

  it("the map's site + opens on Space, prefilled, with focus on its frame", async () => {
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    const plus = mapSiteAdd(container, "site:10.20");
    expect(plus.getAttribute("role")).toBe("button");
    expect(plus.getAttribute("tabindex")).toBe("0");

    plus.focus();
    fireEvent.keyDown(plus, { key: " " });
    const form = await screen.findByTestId("form-add-segment");
    expect(cidrField(form).value).toBe("10.20.0.0/24");
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
  });

  it("the spine is a tab stop and selects on Enter", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    await openSiteAdd("site:10.20");
    const spine = screen.getByTestId("spine").parentElement!;
    expect(spine.getAttribute("role")).toBe("button");
    expect(spine.getAttribute("tabindex")).toBe("0");

    spine.focus();
    fireEvent.keyDown(spine, { key: "Enter" });
    expect(selectionKind()).toBe("segment");
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
  });

  it("the outline's + controls are native buttons -- keyboard-operable by construction", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const adds = await screen.findAllByTestId("outline-site-add");
    // One per site: 10.20, 10.80 and misc.
    expect(adds.map((b) => b.getAttribute("data-site"))).toEqual(["site:10.20", "site:10.80", "site:misc"]);
    for (const button of adds) expect(button.tagName).toBe("BUTTON");
    expect(screen.getByTestId("outline-add-segment").tagName).toBe("BUTTON");
  });

  it("takes focus ONCE per select gesture -- a remount with the same selection leaves focus alone", () => {
    // An apply's cast reload takes the graph away (the actions unmount) and
    // brings it back (they mount again) with the selection the operator made
    // earlier. Focus is on the Apply button by then, and must stay there.
    const graph = buildGraph(trimmedExport, trimmedCast);
    const chosen: Selection = { kind: "segment", cidr: "10.80.20.0/24" };
    const props = {
      graph,
      graphState: { loading: false, error: null, absent: false },
      selection: chosen,
      explicit: chosen,
      canClear: true,
      edits: initialEditState(IMPRINT),
      onStage: () => {},
      onClear: () => {},
      onSelectSegment: () => {},
    };
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    try {
      const first = render(<ElementActions {...props} />);
      expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
      first.unmount();

      elsewhere.focus();
      const second = render(<ElementActions {...props} />);
      expect(document.activeElement).toBe(elsewhere);

      // ...while a NEW gesture on the very same subject still moves it.
      const again: Selection = { kind: "segment", cidr: "10.80.20.0/24" };
      second.rerender(<ElementActions {...props} selection={again} explicit={again} />);
      expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
    } finally {
      elsewhere.remove();
    }
  });

  it("typing in a form does not pull focus back to the heading", async () => {
    // The move is keyed on the SELECTION, not on every render -- a keystroke
    // re-renders the frame, and must not yank the caret out of the field.
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const form = await openSiteAdd("site:10.80");
    const field = cidrField(form);
    field.focus();
    fireEvent.change(field, { target: { value: "10.80.9.0/24" } });
    fireEvent.change(field, { target: { value: "10.80.9.0/2" } });
    expect(document.activeElement).toBe(field);
  });
});

describe("S5.1.a: `clear` is offered only where it changes something", () => {
  it("at level 0 it drops an in-place selection back to nothing", async () => {
    const { container } = render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await screen.findByTestId("level0-svg");
    fireEvent.click(tileSelect(container, "10.80.20.0/24"));
    fireEvent.click(await screen.findByTestId("selection-clear"));
    expect(screen.getByTestId("no-selection")).toBeTruthy();
    expect(screen.queryByTestId("selection-clear")).toBeNull();
  });

  it("where the URL names the subject there is nothing to clear -- until something else takes the slot", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    // A `clear` here would redraw the very same frame: a control that visibly
    // does nothing.
    expect(screen.queryByTestId("selection-clear")).toBeNull();

    await openSiteAdd("site:10.20");
    fireEvent.click(screen.getByTestId("selection-clear"));
    expect(selectionKind()).toBe("segment");
    expect(screen.queryByTestId("selection-clear")).toBeNull();

    // Selecting the spine makes the SAME subject explicit -- still nothing to clear.
    fireEvent.click(screen.getByTestId("spine").parentElement!);
    expect(selectionKind()).toBe("segment");
    expect(screen.queryByTestId("selection-clear")).toBeNull();
  });
});

describe("S5.1.a: a VIEW flag does not drop the selection", () => {
  it("expanding a collapsed group keeps the site + form, and what was typed in it", async () => {
    const { container } = renderWithLocation(`/design/${IMPRINT}`, { exportData: withOverBudgetSite() });
    await screen.findByTestId("level0-svg");
    fireEvent.click(mapSiteAdd(container, "site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    // The operator starts typing, then opens the group to see what it holds.
    fireEvent.change(cidrField(form), { target: { value: "10.80.40.0/24" } });
    fireEvent.click(screen.getByTestId("overflow-tile").parentElement!);

    await waitFor(() => expect(screen.getByTestId("loc").textContent).toBe(`/design/${IMPRINT}?expand=site:10.80`));
    expect(screen.getByTestId("overflow-tile").getAttribute("data-hidden")).toBe("0");
    expect(selectionKind()).toBe("site-add");
    expect(cidrField(screen.getByTestId("form-add-segment")).value).toBe("10.80.40.0/24");
  });

  it("and once expanded, a formerly collapsed segment has its own tile and select control", async () => {
    const { container } = renderWithLocation(`/design/${IMPRINT}?expand=site:10.80`, {
      exportData: withOverBudgetSite(),
    });
    await screen.findByTestId("level0-svg");
    fireEvent.click(tileSelect(container, "10.80.84.0/24"));
    expect((await screen.findByTestId("element-actions")).getAttribute("data-selection")).toBe("segment");
    expect(screen.getByTestId("selection-title").textContent).toBe("Segment 10.80.84.0/24");
  });
});

describe("S5.1.a: with no graph there is nothing to act on, and the inspector says why (LES-253)", () => {
  it("says the topology read FAILED rather than leaving the actions slot silently empty", async () => {
    const boom = new ConsoleApiError({
      kind: "unavailable",
      status: 503,
      message: "the export is not readable",
      url: `/api/imprints/${IMPRINT}/topology`,
    });
    const client: ConsoleApi = { ...fakeApi(), getTopology: () => Promise.reject(boom) };
    render(<TestApp client={client} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    const failed = await screen.findByTestId("actions-failed");
    expect(failed.textContent).toContain("FAILED (unavailable)");
    expect(screen.queryByTestId("actions-loading")).toBeNull();
    expect(screen.queryByTestId("host-actions")).toBeNull();
  });

  it("says it is LOADING while the read is in flight -- not failed, not absent", async () => {
    const client: ConsoleApi = { ...fakeApi(), getTopology: () => new Promise(() => {}) };
    render(<TestApp client={client} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    expect(await screen.findByTestId("actions-loading")).toBeTruthy();
    expect(screen.queryByTestId("actions-failed")).toBeNull();
    expect(screen.queryByTestId("actions-absent")).toBeNull();
  });
});

describe("S5.1.a: a segment's actions emit exactly the S5 records", () => {
  it("*Add host here* stages an `add: host` with the SELECTED segment prefilled", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    const form = await openAction("add-host", "form-add-host");
    fireEvent.change(within(form).getByTestId("host-key"), { target: { value: "address:10.80.20.99" } });
    fireEvent.change(within(form).getByTestId("host-role"), { target: { value: "file-server" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "it answers SMB" } });
    fireEvent.submit(form);

    fireEvent.click(await screen.findByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.document["add"]).toEqual([
      {
        kind: "host",
        key: "address:10.80.20.99",
        segment: "10.80.20.0/24",
        role: "file-server",
        note: "it answers SMB",
      },
    ]);
  });

  it("*Add conduit to…* stages an `add: conduit` with the canonical sorted key", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    const form = await openAction("add-conduit", "form-add-conduit");
    // Half A is the SELECTION, shown rather than typed.
    expect(within(form).getByTestId("conduit-a").textContent).toBe("10.80.20.0/24");
    fireEvent.change(within(form).getByTestId("conduit-b"), { target: { value: "10.80.72.0/24" } });
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "the OT lab reaches the DC" } });
    fireEvent.submit(form);

    fireEvent.click(await screen.findByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.document["add"]).toEqual([
      {
        kind: "conduit",
        key: "10.80.20.0/24|10.80.72.0/24",
        note: "the OT lab reaches the DC",
      },
    ]);
  });

  it("*Suppress hosts…* stages ONE `suppress` record per host, never a batch", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/segments/10.80.20.0-24`} />);
    const form = await openAction("suppress-hosts", "form-suppress-hosts");
    const select = within(form).getByTestId("suppress-host-key") as HTMLSelectElement;
    const chosen = select.value;
    expect(chosen).toBeTruthy();
    fireEvent.change(within(form).getByTestId("gesture-note"), { target: { value: "out of scope" } });
    fireEvent.submit(form);

    const rows = await screen.findAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    fireEvent.click(await screen.findByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.document["suppress"]).toEqual([{ key: chosen, note: "out of scope" }]);
  });
});

// ---------------------------------------------------------------------------
// S5's own acceptance, restated onto the new paths (LES-299)
// ---------------------------------------------------------------------------
describe("the note gate (14 §3: every record needs a non-empty note)", () => {
  it("disables Stage until the note is filled", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    // Path restated: the add-segment form is now born from a site's `+`.
    const seg = await openSiteAdd("site:10.80");
    fireEvent.change(within(seg).getByTestId("seg-cidr"), { target: { value: "10.80.31.0/24" } });
    fireEvent.change(within(seg).getByTestId("seg-name"), { target: { value: "seg-10-80-31" } });
    // No note yet -> Stage is disabled.
    expect((within(seg).getByTestId("stage-button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(within(seg).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    expect((within(seg).getByTestId("stage-button") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("staging a gesture and the change-list tag (15 §8)", () => {
  it("an add-segment stages a pending row tagged `te deploy --update`", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const seg = await openSiteAdd("site:10.80");
    fireEvent.change(within(seg).getByTestId("seg-cidr"), { target: { value: "10.80.31.0/24" } });
    fireEvent.change(within(seg).getByTestId("seg-name"), { target: { value: "seg-10-80-31" } });
    fireEvent.change(within(seg).getByTestId("gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(seg);

    const rows = await screen.findAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute("data-state")).toBe("pending");
    expect(within(rows[0]!).getByTestId("change-tag").textContent).toBe("te deploy --update");
  });

  it("an override (change-role) stages a row tagged `fresh deploy`", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    // Path restated: change-role is an ACTION on the selected host.
    const roleForm = await openAction("change-role", "form-change-role");
    fireEvent.change(within(roleForm).getByTestId("role-value"), { target: { value: "file-server" } });
    fireEvent.change(within(roleForm).getByTestId("gesture-note"), { target: { value: "it answers SMB" } });
    fireEvent.submit(roleForm);

    const row = (await screen.findAllByTestId("change-row"))[0]!;
    expect(row.getAttribute("data-block")).toBe("override");
    expect(within(row).getByTestId("change-tag").textContent).toBe("fresh deploy");
  });

  it("surfaces the pre-flight refusal inline instead of staging", async () => {
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    const seg = await openSiteAdd("site:10.80");
    // A host address with the prefix set is not a network CIDR -> refused.
    fireEvent.change(within(seg).getByTestId("seg-cidr"), { target: { value: "10.80.31.5/24" } });
    fireEvent.change(within(seg).getByTestId("seg-name"), { target: { value: "s" } });
    fireEvent.change(within(seg).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(seg);
    expect(within(seg).getByTestId("gesture-error").textContent).toMatch(/network address/);
    expect(screen.queryByTestId("change-row")).toBeNull();
  });
});

describe("save (PUT .../assertions)", () => {
  it("PUTs the assembled document and marks the record saved", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);

    const suppress = await openAction("suppress", "form-suppress");
    fireEvent.change(within(suppress).getByTestId("gesture-note"), { target: { value: "out of scope" } });
    fireEvent.submit(suppress);

    // The seeded source/as_of come from the existing file, so Save is enabled.
    const save = await screen.findByTestId("save-button");
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(save);

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!.etag).toBe("etag-1");
    expect(puts[0]!.document["suppress"]).toEqual([{ key: "hostname:axdc01", note: "out of scope" }]);
    // The row is now `saved`.
    const row = (await screen.findAllByTestId("change-row"))[0]!;
    await waitFor(() => expect(row.getAttribute("data-state")).toBe("saved"));
  });
});

describe("a stale-etag save (412)", () => {
  it("surfaces a reload prompt, does NOT mark the record saved, and gates the retry", async () => {
    const reject: ConsoleApi["putAssertions"] = () =>
      Promise.reject(
        new ConsoleApiError({
          kind: "stale-etag",
          status: 412,
          message: "the file changed since you loaded it (on disk: etag-9)",
          detail: { etag: "etag-9" },
          etag: "etag-9",
          url: "",
        }),
      );
    render(<TestApp client={fakeApi({ putAssertions: reject })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);

    const suppress = await openAction("suppress", "form-suppress");
    fireEvent.change(within(suppress).getByTestId("gesture-note"), { target: { value: "out of scope" } });
    fireEvent.submit(suppress);

    const save = await screen.findByTestId("save-button");
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(save);

    // The reload prompt shows, naming the on-disk etag; the record stays
    // pending (the write did not take effect); the retry is gated.
    await screen.findByTestId("reload-button");
    expect(screen.getByTestId("edit-conflict").textContent).toMatch(/etag-9/);
    const row = (await screen.findAllByTestId("change-row"))[0]!;
    expect(row.getAttribute("data-state")).toBe("pending");
    await waitFor(() => expect((screen.getByTestId("save-button") as HTMLButtonElement).disabled).toBe(true));
  });
});

describe("removing an assertion (15 §8: the record is deleted from the file)", () => {
  it("discards a PENDING record outright, with no PUT", async () => {
    const puts: FakeOpts["puts"] = [];
    render(<TestApp client={fakeApi({ puts })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    const suppress = await openAction("suppress", "form-suppress");
    fireEvent.change(within(suppress).getByTestId("gesture-note"), { target: { value: "n" } });
    fireEvent.submit(suppress);

    const row = (await screen.findAllByTestId("change-row"))[0]!;
    fireEvent.click(within(row).getByTestId("change-remove"));
    await waitFor(() => expect(screen.queryByTestId("change-row")).toBeNull());
    expect(puts).toHaveLength(0);
  });

  it("re-saves the file WITHOUT a saved record when it is removed", async () => {
    const puts: FakeOpts["puts"] = [];
    const seeded = existingDoc({ suppress: [{ key: "hostname:axdc01", note: "was suppressed" }] });
    render(<TestApp client={fakeApi({ puts, assertions: seeded })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);

    // The loaded file's record shows as saved.
    const row = (await screen.findAllByTestId("change-row"))[0]!;
    await waitFor(() => expect(row.getAttribute("data-state")).toBe("saved"));
    fireEvent.click(within(row).getByTestId("change-remove"));

    await waitFor(() => expect(puts).toHaveLength(1));
    // The re-saved document no longer carries the suppress.
    expect(puts[0]!.document["suppress"]).toEqual([]);
    await waitFor(() => expect(screen.queryByTestId("change-row")).toBeNull());
  });

  it("removing a SAVED record does NOT commit unsaved PENDING edits (S1)", async () => {
    // Design 15 §8: pending = browser only. `documentFrom` collects EVERY
    // entry, so a naive removal document would write the pending override too
    // and `saveSucceeded` would flip it to saved. The removal must PUT only
    // the file's records (saved + applied) minus the removed one.
    const puts: FakeOpts["puts"] = [];
    const seeded = existingDoc({ suppress: [{ key: "hostname:axdc01", note: "was suppressed" }] });
    render(<TestApp client={fakeApi({ puts, assertions: seeded })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);

    // The saved suppress loads; stage a PENDING override on top of it.
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    const roleForm = await openAction("change-role", "form-change-role");
    fireEvent.change(within(roleForm).getByTestId("role-value"), { target: { value: "file-server" } });
    fireEvent.change(within(roleForm).getByTestId("gesture-note"), { target: { value: "it answers SMB" } });
    fireEvent.submit(roleForm);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(2));

    // Remove the SAVED suppress row.
    const savedRow = screen.getAllByTestId("change-row").find((r) => r.getAttribute("data-block") === "suppress")!;
    fireEvent.click(within(savedRow).getByTestId("change-remove"));

    await waitFor(() => expect(puts).toHaveLength(1));
    // The PUT commits ONLY the removal -- the pending override is not in it.
    expect(puts[0]!.document["suppress"]).toEqual([]);
    expect(puts[0]!.document["override"]).toEqual([]);
    // ...and the pending override is still pending, not silently saved.
    const rows = screen.getAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute("data-block")).toBe("override");
    expect(rows[0]!.getAttribute("data-state")).toBe("pending");
  });

  it("does NOT re-save a removal when the save metadata is invalid (S2, parity with Save)", async () => {
    const puts: FakeOpts["puts"] = [];
    const seeded = existingDoc({ suppress: [{ key: "hostname:axdc01", note: "was suppressed" }] });
    render(<TestApp client={fakeApi({ puts, assertions: seeded })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);

    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    // Invalidate the source (no operator name): Save disables, and the
    // remove-that-re-saves must be gated by the SAME check.
    fireEvent.change(screen.getByTestId("save-source"), { target: { value: "operator: " } });
    await waitFor(() => expect((screen.getByTestId("save-button") as HTMLButtonElement).disabled).toBe(true));

    const savedRow = screen.getAllByTestId("change-row")[0]!;
    fireEvent.click(within(savedRow).getByTestId("change-remove"));

    // The handler returns early -- no PUT, and the row stays.
    await Promise.resolve();
    expect(puts).toHaveLength(0);
    expect(screen.getAllByTestId("change-row")).toHaveLength(1);
  });
});
