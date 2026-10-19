/**
 * S5.1.a's combined fix round (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a,
 * iteration 7), through the real shell:
 *
 *   - **A1** the inspector's deployed-CIDR / zone / cast facts come from
 *     `GET …/cast`'s segment roster -- the production path, with no fixture map
 *     (LES-311);
 *   - **A2** a segment the cast carries is refused, one it PRUNED is re-addable
 *     (`docs/design/14-operator-assertions.md` §3's own example);
 *   - **A3** a segment the file adds is selectable and actionable: the whole
 *     `+` → *Add host here* → *Add conduit to…* flow, the outline, a direct
 *     URL and the conduit peer picker;
 *   - **A4** a name prefill never duplicates a segment name;
 *   - **B1** the claim note and the Stage check read one canonical value;
 *   - **B2** a selection survives an apply and every dimming filter;
 *   - **B3** Escape clears a selection before it leaves a level;
 *   - **B4** a bundle with no topology export is "not applicable", not failed;
 *   - **B5** the map's controls are path icons, never a text glyph;
 *   - **C** 24×24 targets, one label for the site `+`, `aria-current`, focus
 *     return, and a site's form isolated by its key.
 *
 * The fake API is STATEFUL (a PUT or an apply rewrites the file a later GET
 * reads), like `selection.ui.test.tsx`'s -- the apply cases ask what survives a
 * re-read, which a fake that always answers the seed file cannot say.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type {
  ApplyResponse,
  AssertionsDoc,
  CastSummary,
  ImprintDiff,
  ImprintList,
  ImprintProvenance,
  TopologyExport,
} from "../src/api/types";
import { buildRecord, type Gesture } from "../src/edit/records";
import { documentFrom, editReducer, initialEditState } from "../src/edit/store";
import { ConsoleRoutes, TestApp } from "../src/routes";
import { trimmedCast, trimmedExport, withOverBudgetSite } from "./fixtures/trimmed";

const IMPRINT = "trimmed-fixture";
const AS_OF = "2026-09-08T00:00:00Z";
const SOURCE = "operator: tester";

/** The trimmed twin's roster as a server publishes it (A1): every observed
 *  segment but `10.80.72.0/24`, which the cast PRUNED, relocated into 10.200,
 *  plus the cast's own unobserved `internet-sink`. */
const ROSTER = [
  { name: "seg-10-1-10-0", cidr: "10.200.0.0/24", zone_type: "simple" },
  { name: "seg-10-20-192-0", cidr: "10.200.1.0/24", zone_type: null },
  { name: "seg-10-20-193-0", cidr: "10.200.2.0/24", zone_type: "vlan" },
  { name: "seg-10-80-20-0", cidr: "10.200.3.0/24", zone_type: "simple" },
  { name: "seg-192-168-0-0", cidr: "10.200.4.0/24", zone_type: "simple" },
  { name: "internet-sink", cidr: "10.200.254.0/24", zone_type: "simple" },
];

function castFor(id: string, extra: Partial<CastSummary> = {}): CastSummary {
  return {
    id,
    bundle_dir: `/b/${id}`,
    twin_config_id: id,
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
    ...extra,
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
    as_of: AS_OF,
    source: SOURCE,
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

interface ServerOpts {
  id?: string;
  file?: Partial<AssertionsDoc>;
  cast?: Partial<CastSummary>;
  exportData?: TopologyExport;
  getTopology?: ConsoleApi["getTopology"];
  changed?: boolean;
}

function server(opts: ServerOpts = {}): { api: ConsoleApi; puts: Array<Record<string, unknown>>; applies: unknown[] } {
  const id = opts.id ?? IMPRINT;
  let file = fileDoc(id, opts.file);
  let writes = 1;
  const puts: Array<Record<string, unknown>> = [];
  const applies: unknown[] = [];
  const write = (document: Record<string, unknown>): AssertionsDoc => {
    writes += 1;
    file = fileDoc(id, {
      etag: `etag-${writes}`,
      add: (document["add"] as AssertionsDoc["add"]) ?? [],
      override: (document["override"] as AssertionsDoc["override"]) ?? [],
      suppress: (document["suppress"] as AssertionsDoc["suppress"]) ?? [],
    });
    return file;
  };
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const provenance: ImprintProvenance = {
    imprint_id: id,
    rows: [],
    sidecar_shape: "absent",
    sidecar_schema: null,
    evidence_source: "none",
    code_fingerprint: null,
    population: {},
    warnings: [],
  };
  const api: ConsoleApi = {
    listImprints: () =>
      Promise.resolve({
        imprints: [{ id, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" }],
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(provenance),
    getTopology: opts.getTopology ?? (() => Promise.resolve(opts.exportData ?? (trimmedExport as TopologyExport))),
    getCast: (castId) => Promise.resolve(castFor(castId, opts.cast)),
    getAssertions: () => Promise.resolve(file),
    putAssertions: (_id, document) => {
      puts.push(document);
      return Promise.resolve(write(document));
    },
    getLayout: () =>
      Promise.resolve({ imprint_id: id, path: "", exists: false, etag: null, schema: "axr-layout/1", imprint: id, pins: {}, plane: null }),
    putLayout: unused("putLayout"),
    apply: (_id, req) => {
      const document = req.document as Record<string, unknown>;
      applies.push(document);
      const assertions = write(document);
      return Promise.resolve({
        imprint_id: id,
        fingerprint_before: "fp0",
        fingerprint_after: opts.changed === false ? "fp0" : "fp1",
        changed: opts.changed ?? true,
        pit_code_fingerprint_before: null,
        pit_code_fingerprint_after: null,
        cast_code_fingerprint_before: "build-a",
        cast_code_fingerprint_after: "build-a",
        compiler_changed: false,
        compiler_note: null,
        assertions,
        record_counts: {},
        cast_counts: {},
        stdout_json: null,
        stderr: "",
        returncode: 0,
        argv: [],
        install_argv: [],
        install_returncode: null,
        history_written: true,
        warnings: [],
      } satisfies ApplyResponse);
    },
    getDiff: () =>
      Promise.resolve({
        imprint_id: id,
        since: "fp0",
        current_fingerprint: "fp1",
        snapshot_schema: "1",
        source: "topology export",
        observed: null,
        twin: null,
        suppressed_keys: null,
        twin_config_changed: false,
        warnings: [],
      } satisfies ImprintDiff),
  };
  return { api, puts, applies };
}

function LocationProbe(): JSX.Element {
  const loc = useLocation();
  return <output data-testid="loc">{`${loc.pathname}${loc.search}`}</output>;
}

function open(url: string, api: ConsoleApi = server().api): ReturnType<typeof render> {
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
const navOf = (c: HTMLElement, selector: string): HTMLElement => {
  const rect = c.querySelector(selector);
  if (!rect?.parentElement) throw new Error(`nothing matches ${selector}`);
  return rect.parentElement;
};
const tilePencil = (c: HTMLElement, cidr: string): HTMLElement =>
  navOf(c, `[data-testid="tile-actions"][data-cidr="${cidr}"]`);
const mapPlus = (c: HTMLElement, site: string): HTMLElement => navOf(c, `[data-testid="site-add"][data-site="${site}"]`);
async function outlinePlus(site: string): Promise<HTMLElement> {
  const adds = await screen.findAllByTestId("outline-site-add");
  const button = adds.find((b) => b.getAttribute("data-site") === site);
  if (!button) throw new Error(`no outline + for ${site}`);
  return button;
}
const title = (): string | null => screen.queryByTestId("selection-title")?.textContent ?? null;
const selectionKind = (): string | null =>
  screen.queryByTestId("element-actions")?.getAttribute("data-selection") ?? null;
const input = (scope: HTMLElement, id: string): HTMLInputElement => within(scope).getByTestId(id) as HTMLInputElement;
const liveForm = (): HTMLElement => screen.getByTestId("form-add-segment");

/** The value cell of a facts-table row, by its label. */
function fact(table: HTMLElement, label: string): string {
  const row = [...table.querySelectorAll("tr")].find((tr) => tr.querySelector("th")?.textContent === label);
  if (!row) throw new Error(`no fact row ${label}`);
  return row.querySelectorAll("td")[0]?.textContent ?? "";
}

async function stageForm(form: HTMLElement, rows: number): Promise<void> {
  fireEvent.submit(form);
  await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(rows));
}

// ===========================================================================
// A1 -- the roster reaches the inspector through the seam the app reads it by
// ===========================================================================
describe("A1: the inspector's cast facts come from GET …/cast's segment roster", () => {
  it("states a carried segment's declared CIDR and zone -- true on a real-shaped cast, never 'not declared'", async () => {
    // Restated for the second fix round (LES-299): "declared", not "deployed" --
    // a segment the deploy gate removes is declared in the cast all the same.
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, server({ cast: { segment_roster: ROSTER } }).api);
    const facts = await screen.findByTestId("segment-facts");
    expect(fact(facts, "declared in the cast")).toBe("yes");
    expect(fact(facts, "declared as")).toBe("10.200.3.0/24");
    expect(fact(facts, "zone")).toBe("simple");
  });

  it("a carried segment whose config declares no zone says exactly that", async () => {
    open(`/design/${IMPRINT}/segments/10.20.192.0-24`, server({ cast: { segment_roster: ROSTER } }).api);
    const facts = await screen.findByTestId("segment-facts");
    expect(fact(facts, "declared as")).toBe("10.200.1.0/24");
    expect(fact(facts, "zone")).toBe("not declared in the config");
  });

  it("a segment the roster does not name says 'renamed or pruned' -- never 'restores', no CIDR invented", async () => {
    // Restated for MF2 + SF1 (LES-299): the compiler keeps an observed segment
    // only when a host is placed on it, and the cast joins by NAME, so a
    // missing name is "renamed or pruned", not a finding of "pruned".
    open(`/design/${IMPRINT}/segments/10.80.72.0-24`, server({ cast: { segment_roster: ROSTER } }).api);
    const facts = await screen.findByTestId("segment-facts");
    expect(fact(facts, "declared in the cast")).toBe(
      "not under this name — renamed or pruned (the cast joins by name); an add sets its name and zone type, " +
        "but the compiler keeps a segment only when a host is placed on it: use Add host here",
    );
    expect(fact(facts, "declared in the cast")).not.toMatch(/restor/);
    expect(fact(facts, "declared as")).toBe("— (not in the cast under this name)");
    expect(fact(facts, "zone")).toBe("— (not in the cast under this name)");
  });

  it("a server that publishes NO roster is said to be that, rather than a twin that declares nothing", async () => {
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`);
    const facts = await screen.findByTestId("segment-facts");
    expect(fact(facts, "declared in the cast")).toBe("not published by this server");
    expect(fact(facts, "declared as")).toBe("not published by this server");
    expect(fact(facts, "zone")).toBe("not published by this server");
  });
});

// ===========================================================================
// A2 -- refuse only what is really taken
// ===========================================================================
describe("A2: a segment the cast declares is refused; one it does not declare under its name is allowed, honestly", () => {
  it("refuses a declared CIDR, pointing at Add host here -- and allows, says and stages one the roster does not name", async () => {
    // Restated for MF2, SF1 and ZT (LES-299): the note no longer promises a
    // restore (the compiler keeps an observed segment only once a host is placed
    // on it), a missing name is "renamed or pruned", and the zone type starts at
    // the site's dominant declared zone (`simple` here), not `vlan`.
    const { api, puts } = server({ cast: { segment_roster: ROSTER } });
    open(`/design/${IMPRINT}`, api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    // The suggestion skips BOTH observed segments of 10.80, declared or not.
    expect(input(form, "seg-cidr").value).toBe("10.80.0.0/24");

    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.20.0/24" } });
    expect(within(form).getByTestId("seg-claim").textContent).toBe(
      "10.80.20.0/24 is already declared in the cast — select it and use Add host here. Stage refuses it.",
    );
    fireEvent.change(input(form, "gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    expect(within(form).getByTestId("gesture-error").textContent).toBe(
      "10.80.20.0/24 is already declared in the cast — select it and use Add host here",
    );
    expect(screen.queryByTestId("change-row")).toBeNull();

    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.72.0/24" } });
    const note = within(form).getByTestId("seg-claim");
    expect(note.textContent).toBe(
      "10.80.72.0/24 is observed, but the installed cast does not declare it under this name — renamed or pruned " +
        "(the cast joins by name). An add sets its name and zone type, but the compiler keeps a segment only when a " +
        "host is placed on it: select it and use Add host here.",
    );
    expect(note.textContent).not.toMatch(/restor/);
    expect(note.className).toBe("meta");
    // Under its own observed name: not a collision.
    expect(input(form, "seg-name").value).toBe("seg-10-80-72-0");
    expect(within(form).queryByTestId("seg-name-claim")).toBeNull();
    // ZT: the zone type 10.80's declared segments use.
    expect((within(form).getByTestId("seg-zone") as HTMLSelectElement).value).toBe("simple");
    fireEvent.change(input(form, "gesture-note"), { target: { value: "not in the cast; it is the OT lab" } });
    await stageForm(form, 1);

    // Selected, and its facts say what the add does -- and what keeps the segment.
    expect(title()).toBe("Segment 10.80.72.0/24");
    expect(fact(screen.getByTestId("segment-facts"), "declared in the cast")).toBe(
      "not under this name — renamed or pruned (the cast joins by name); a pending add in this file names it, and " +
        "an add sets its name and zone type, but the compiler keeps a segment only when a host is placed on it: " +
        "use Add host here",
    );
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([
      { kind: "segment", key: "10.80.72.0/24", name: "seg-10-80-72-0", zone_type: "simple", note: "not in the cast; it is the OT lab" },
    ]);
  });

  it("the note's `select` control opens the undeclared segment, whose Add host here is the gesture that keeps it", async () => {
    const { api, puts } = server({ cast: { segment_roster: ROSTER } });
    open(`/design/${IMPRINT}`, api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.72.0/24" } });
    fireEvent.click(screen.getByTestId("seg-claim-select"));
    expect(title()).toBe("Segment 10.80.72.0/24");
    expect(screen.queryByTestId("form-add-segment")).toBeNull();

    fireEvent.click(screen.getByTestId("action-open-add-host"));
    const host = await screen.findByTestId("form-add-host");
    fireEvent.change(input(host, "host-key"), { target: { value: "address:10.80.72.50" } });
    fireEvent.change(input(host, "gesture-note"), { target: { value: "a host keeps the segment" } });
    await stageForm(host, 1);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([
      { kind: "host", key: "address:10.80.72.50", segment: "10.80.72.0/24", note: "a host keeps the segment" },
    ]);
  });

  it("never PREFILLS a segment the cast does not declare -- even when it IS the site's lowest /24 -- while typing it stays allowed", async () => {
    // The test above cannot tell: its pruned segment (10.80.72.0/24) is not the
    // lowest /24 of the site, so a suggestion that ignored pruned claims would
    // still offer 10.80.0.0/24 and pass. Here the pruned segment IS 10.80.0.0/24
    // -- exactly what a pruned-blind allocator would propose (iteration-7
    // tester: that planted breakage survived the whole suite before this test).
    const prunedAtZero = "10.80.0.0/24";
    const exportData: TopologyExport = {
      ...trimmedExport,
      segments: [
        ...trimmedExport.segments,
        { cidr: prunedAtZero, name: "seg-10-80-0-0", entities: 1, coverage_source: "inferred_from_traffic" },
      ],
      nodes: [
        ...trimmedExport.nodes,
        {
          id: "address:10.80.0.5",
          address: "10.80.0.5",
          locality: "local",
          segment: prunedAtZero,
          segment_evidence_class: "observed",
          role: "generic-endpoint",
          role_confidence: 0.7,
          role_evidence_class: "imputed",
          windows_seen: 10,
          listen_ports: [],
          dial_ports: [],
        },
      ],
    };
    // Precondition, stated rather than assumed: the roster does not name it.
    expect(ROSTER.some((seg) => seg.name === "seg-10-80-0-0")).toBe(false);

    open(`/design/${IMPRINT}`, server({ cast: { segment_roster: ROSTER }, exportData }).api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    expect(input(form, "seg-cidr").value).toBe("10.80.1.0/24");
    expect(screen.getByTestId("site-add-suggestion").textContent).toContain("10.80.1.0/24");
    // The prefill touches nothing in play: no note at all.
    expect(within(form).queryByTestId("seg-claim")).toBeNull();

    // Typed by hand, the undeclared segment is allowed -- never refused. (Its
    // wording restated for MF2, LES-299: not "the restore".)
    fireEvent.change(input(form, "seg-cidr"), { target: { value: prunedAtZero } });
    const note = within(form).getByTestId("seg-claim");
    expect(note.className).toBe("meta");
    expect(note.textContent).toMatch(/^10\.80\.0\.0\/24 is observed, but the installed cast does not declare it under this name/);
  });
});

// ===========================================================================
// A3 -- a segment the file adds is actionable
// ===========================================================================
describe("A3: a segment the file adds can be selected, given hosts and conduits, and found again", () => {
  it("+ → stage → Add host here → stage → Add conduit to… → stage writes exactly the S5 record layer's document", async () => {
    const { api, puts } = server();
    open(`/design/${IMPRINT}`, api);

    fireEvent.click(await outlinePlus("site:10.80"));
    let form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.31.0/24" } });
    expect(input(form, "seg-name").value).toBe("seg-10-80-31-0");
    fireEvent.change(input(form, "gesture-note"), { target: { value: "the OT lab" } });
    await stageForm(form, 1);

    // The new segment IS the selection: its frame, its record, focus on it.
    expect(title()).toBe("Segment 10.80.31.0/24");
    expect(selectionKind()).toBe("segment");
    const facts = screen.getByTestId("added-segment-facts");
    expect(fact(facts, "record")).toBe("pending add");
    expect(fact(facts, "staged hosts")).toBe("0");
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
    expect(screen.queryByTestId("selection-missing")).toBeNull();
    // Nothing it cannot do is offered as if it could.
    expect(screen.getByTestId("action-open-suppress-hosts").getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByTestId("action-open-clone").getAttribute("aria-disabled")).toBe("true");

    fireEvent.click(screen.getByTestId("action-open-add-host"));
    form = await screen.findByTestId("form-add-host");
    expect(within(form).getByText("Add a host on 10.80.31.0/24")).toBeTruthy();
    fireEvent.change(input(form, "host-key"), { target: { value: "address:10.80.31.10" } });
    fireEvent.change(input(form, "host-role"), { target: { value: "plc" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "the OT lab's PLC" } });
    await stageForm(form, 2);
    expect(fact(screen.getByTestId("added-segment-facts"), "staged hosts")).toBe("1");

    fireEvent.click(screen.getByTestId("action-open-add-conduit"));
    form = await screen.findByTestId("form-add-conduit");
    expect(within(form).getByTestId("conduit-a").textContent).toBe("10.80.31.0/24");
    fireEvent.change(within(form).getByTestId("conduit-b"), { target: { value: "10.80.20.0/24" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "the OT lab reaches the DC" } });
    await stageForm(form, 3);

    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    const written = JSON.stringify(puts[0]);

    // (a) The S5 record layer, fed the same values with no UI in between.
    const gestures: Gesture[] = [
      // No roster declares a zone, so the form started at `simple` (restated for
      // the zone fallback, LES-299).
      { kind: "add-segment", cidr: "10.80.31.0/24", name: "seg-10-80-31-0", zoneType: "simple", note: "the OT lab" },
      { kind: "add-host", key: "address:10.80.31.10", segment: "10.80.31.0/24", role: "plc", note: "the OT lab's PLC" },
      { kind: "add-conduit", cidrA: "10.80.31.0/24", cidrB: "10.80.20.0/24", note: "the OT lab reaches the DC" },
    ];
    let state = editReducer(initialEditState(IMPRINT), { type: "loaded", doc: fileDoc(IMPRINT) });
    for (const g of gestures) {
      const built = buildRecord(g);
      if (!built.ok) throw new Error(`${g.kind}: ${built.error}`);
      state = editReducer(state, { type: "stage", block: built.staged.block, record: built.staged.record });
    }
    expect(written).toBe(JSON.stringify(documentFrom(state, { schema: "axr-assertions/1", imprint: IMPRINT, as_of: AS_OF, source: SOURCE })));
    // (b) The literal document -- the non-circular anchor (LES-187).
    expect(written).toBe(
      JSON.stringify({
        schema: "axr-assertions/1",
        imprint: IMPRINT,
        as_of: AS_OF,
        source: SOURCE,
        add: [
          { kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31-0", zone_type: "simple", note: "the OT lab" },
          { kind: "host", key: "address:10.80.31.10", segment: "10.80.31.0/24", role: "plc", note: "the OT lab's PLC" },
          { kind: "conduit", key: "10.80.20.0/24|10.80.31.0/24", note: "the OT lab reaches the DC" },
        ],
        override: [],
        suppress: [],
        etag: "etag-1",
      }),
    );
  });

  it("the outline lists it under the site it would group with, with its state, and a click selects it in place", async () => {
    const file = { add: [{ kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31-0", zone_type: "vlan", note: "from the file" }] };
    open(`/design/${IMPRINT}`, server({ file }).api);
    const row = await screen.findByTestId("outline-added-segment");
    // Under the 10.80 site: the block holding that site's `+`.
    const site = (await outlinePlus("site:10.80")).closest(".tree-site")!;
    expect(site.contains(row)).toBe(true);
    expect(row.getAttribute("data-cidr")).toBe("10.80.31.0/24");
    expect(within(row).getByTestId("outline-added-state").textContent).toBe("saved add");

    fireEvent.click(row);
    expect(title()).toBe("Segment 10.80.31.0/24");
    expect(loc()).toBe(`/design/${IMPRINT}`); // selected in place, not drilled
    expect(screen.getByTestId("outline-added-segment").getAttribute("aria-current")).toBe("true");
  });

  it("one whose prefix only a misc singleton shares is listed where it would group -- never dropped", async () => {
    const file = { add: [{ kind: "segment", key: "10.99.1.0/24", name: "lab", note: "a new prefix" }] };
    open(`/design/${IMPRINT}`, server({ file }).api);
    const row = await screen.findByTestId("outline-added-segment");
    const misc = (await outlinePlus("site:misc")).closest(".tree-site")!;
    expect(misc.contains(row)).toBe(true);
  });

  it("a direct URL selects it, and the MAP now DRAWS it as a hatched spine (S5.1.d)", async () => {
    // RESTATED (LES-299): this asserted the notice that said level 1 "does not
    // draw added segments yet". S5.1.d draws them -- a hatched spine with the
    // hosts staged on it -- so the property under test moves from the sentence
    // to the drawing, and the notice is gone.
    const file = { add: [{ kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31-0", note: "from the file" }] };
    open(`/design/${IMPRINT}/segments/10.80.31.0-24`, server({ file }).api);
    const svg = await screen.findByTestId("level1-svg");
    expect(svg.getAttribute("aria-label")).toBe("Level 1 for 10.80.31.0/24, a segment this file adds");
    const spine = screen.getByTestId("added-spine");
    expect(spine.getAttribute("data-cidr")).toBe("10.80.31.0/24");
    expect(spine.getAttribute("data-state")).toBe("saved");
    expect(screen.queryByTestId("added-not-drawn")).toBeNull();
    expect(screen.getByTestId("added-no-hosts").textContent).toMatch(/no hosts staged on it yet/);
    expect(screen.queryByTestId("no-segment")).toBeNull();
    expect(await screen.findByTestId("added-segment-facts")).toBeTruthy();
    expect(screen.queryByTestId("selection-missing")).toBeNull();
    expect(screen.getByTestId("action-open-add-host").hasAttribute("aria-disabled")).toBe(false);
  });

  it("an observed segment's conduit picker offers it, labelled as an add", async () => {
    const file = { add: [{ kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31-0", note: "from the file" }] };
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, server({ file }).api);
    fireEvent.click(await screen.findByTestId("action-open-add-conduit"));
    const select = within(await screen.findByTestId("form-add-conduit")).getByTestId("conduit-b") as HTMLSelectElement;
    const option = [...select.options].find((o) => o.value === "10.80.31.0/24");
    expect(option?.textContent).toBe("10.80.31.0/24 (saved add)");
    // Observed peers first, so the default is still an observed one.
    expect(select.options[0]!.value).toBe("10.1.10.0/24");
  });
});

// ===========================================================================
// A4 -- names
// ===========================================================================
describe("A4: the name prefill never duplicates a segment name, and a typed clash is refused", () => {
  it("prefills a /23 and a /25 at an observed network address collision-free, and refuses a clash with STR-095", async () => {
    open(`/design/${IMPRINT}`);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.20.0/23" } });
    expect(input(form, "seg-name").value).toBe("seg-10-80-20-0-23");
    expect(within(form).queryByTestId("seg-name-claim")).toBeNull();
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.20.0/25" } });
    expect(input(form, "seg-name").value).toBe("seg-10-80-20-0-25");
    // A plain /24 elsewhere keeps the producer's convention.
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.50.0/24" } });
    expect(input(form, "seg-name").value).toBe("seg-10-80-50-0");

    // Typed over: the name of the /24 the export observes.
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.20.0/25" } });
    fireEvent.change(input(form, "seg-name"), { target: { value: "seg-10-80-20-0" } });
    const clash = within(form).getByTestId("seg-name-claim").textContent ?? "";
    expect(clash).toMatch(/^the name seg-10-80-20-0 is already used by 10\.80\.20\.0\/24, an observed segment/);
    expect(clash).toMatch(/STR-095/);
    fireEvent.change(input(form, "gesture-note"), { target: { value: "n" } });
    fireEvent.submit(form);
    expect(within(form).getByTestId("gesture-error").textContent).toMatch(/STR-095/);
    expect(screen.queryByTestId("change-row")).toBeNull();
  });
});

// ===========================================================================
// B1 -- one canonical value
// ===========================================================================
describe("B1: the claim note and the Stage check read ONE canonical value, at every prefix of a typed CIDR", () => {
  it("says nothing about a value that is not a network CIDR, and exactly what Stage says about one that is", async () => {
    open(`/design/${IMPRINT}`);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "gesture-note"), { target: { value: "n" } });
    const typed = "10.80.20.0/24";
    const values = [
      ...Array.from({ length: typed.length }, (_, i) => typed.slice(0, i + 1)),
      "10.80.31.0/",
      "10.80.20.5/24",
      "10.80.20.5/25",
    ];
    // Every claim phrasing `claimMessage` has -- restated with its wording
    // (LES-299), or this negative check would pass against words that no
    // longer exist.
    const claimWords = /is observed in this twin's export|is already declared in the cast|does not declare it under this name|overlaps|claimed by/;
    for (const value of values) {
      fireEvent.change(input(liveForm(), "seg-cidr"), { target: { value } });
      const note = within(liveForm()).queryByTestId("seg-claim")?.textContent ?? null;
      fireEvent.submit(liveForm());
      const error = within(liveForm()).queryByTestId("gesture-error")?.textContent ?? null;
      if (value === typed) {
        // The one network CIDR here: the note and the refusal are one sentence.
        expect(note, value).not.toBeNull();
        expect(note, value).toBe(`${error}. Stage refuses it.`);
      } else {
        // A half-typed value, a /0-by-accident, host bits set: no note -- and
        // the refusal, whatever it is, is not a claim about a network.
        expect(note, value).toBeNull();
        expect(error ?? "", value).not.toMatch(claimWords);
      }
    }
    expect(screen.queryByTestId("change-row")).toBeNull();
  });
});

// ===========================================================================
// B2 -- selection lifetime
// ===========================================================================
describe("B2: a selection, and what was typed under it, lives as long as the PATHNAME", () => {
  it("survives an apply that changes the twin -- `?changed=1` and the cast re-read included", async () => {
    const { api, applies } = server({ changed: true });
    const { container } = open(`/design/${IMPRINT}`, api);
    await screen.findByTestId("level0-svg");
    // Something for the apply to save first.
    fireEvent.click(tilePencil(container, "10.80.20.0/24"));
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const hostForm = await screen.findByTestId("form-add-host");
    fireEvent.change(input(hostForm, "host-key"), { target: { value: "address:10.80.20.99" } });
    fireEvent.change(input(hostForm, "gesture-note"), { target: { value: "n" } });
    await stageForm(hostForm, 1);

    fireEvent.click(mapPlus(container, "site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.40.0/24" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "typed before the apply" } });

    fireEvent.click(screen.getByTestId("apply-button"));
    await waitFor(() => expect(applies).toHaveLength(1));
    await waitFor(() => expect(loc()).toBe(`/design/${IMPRINT}?changed=1`));
    expect(selectionKind()).toBe("site-add");
    expect(input(liveForm(), "seg-cidr").value).toBe("10.80.40.0/24");
    expect(input(liveForm(), "gesture-note").value).toBe("typed before the apply");
  });

  it("is held through a SLOW cast re-read -- the frame and its typed input never unmount while the apply reloads", async () => {
    // The apply test above cannot tell a held graph from a dropped one: its
    // fake answers the re-read at once, and React batches the clear with the
    // refill, so no render ever sees the graph gone. Here the re-read is held
    // OPEN until the test releases it -- the window a real network has.
    const base = server({ changed: true });
    let casts = 0;
    let release: () => void = () => {
      throw new Error("the cast was never re-read");
    };
    const api: ConsoleApi = {
      ...base.api,
      getCast: (id) => {
        casts += 1;
        if (casts === 1) return base.api.getCast(id);
        return new Promise<CastSummary>((resolve) => {
          release = () => resolve(castFor(id));
        });
      },
    };
    const { container } = open(`/design/${IMPRINT}`, api);
    await screen.findByTestId("level0-svg");
    fireEvent.click(mapPlus(container, "site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.40.0/24" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "typed before the apply" } });

    fireEvent.click(screen.getByTestId("apply-button"));
    await waitFor(() => expect(casts).toBe(2));
    // The re-read is in flight: nothing unmounted, nothing lost.
    expect(screen.queryByTestId("actions-loading")).toBeNull();
    expect(selectionKind()).toBe("site-add");
    expect(input(liveForm(), "seg-cidr").value).toBe("10.80.40.0/24");
    expect(screen.getByTestId("level0-svg")).toBeTruthy();

    release();
    await waitFor(() => expect(loc()).toBe(`/design/${IMPRINT}?changed=1`));
    await waitFor(() => expect(screen.getByTestId("summary-digest")).toBeTruthy());
    expect(input(liveForm(), "seg-cidr").value).toBe("10.80.40.0/24");
    expect(input(liveForm(), "gesture-note").value).toBe("typed before the apply");
  });

  it("survives every DIMMING filter -- no filter hides a segment", async () => {
    const rows: Array<{ chip: string; after: string }> = [
      { chip: "ghosts", after: `/design/${IMPRINT}?ghosts=1` },
      { chip: "imputed", after: `/design/${IMPRINT}?class=imputed` },
      { chip: "0.5", after: `/design/${IMPRINT}?occupancy_lt=0.5` },
    ];
    for (const row of rows) {
      const { container, unmount } = open(`/design/${IMPRINT}`);
      await screen.findByTestId("level0-svg");
      fireEvent.click(mapPlus(container, "site:10.80"));
      const form = await screen.findByTestId("form-add-segment");
      fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.40.0/24" } });
      fireEvent.change(input(form, "gesture-note"), { target: { value: "typed before the filter" } });
      fireEvent.click(within(screen.getByTestId("filters")).getByText(row.chip));
      await waitFor(() => expect(loc(), row.chip).toBe(row.after));
      expect(selectionKind(), row.chip).toBe("site-add");
      expect(input(liveForm(), "seg-cidr").value, row.chip).toBe("10.80.40.0/24");
      expect(input(liveForm(), "gesture-note").value, row.chip).toBe("typed before the filter");
      unmount();
    }
  });
});

// ===========================================================================
// B3 -- Escape
// ===========================================================================
describe("B3: Escape clears a selection before it leaves a level", () => {
  it("an explicit selection is cleared first -- the level stays, pending edits survive, focus returns to the opener", async () => {
    const { container } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    const pencil = tilePencil(container, "10.80.20.0/24");
    pencil.focus();
    fireEvent.keyDown(pencil, { key: "Enter" });
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    fireEvent.change(input(form, "host-key"), { target: { value: "address:10.80.20.99" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "pending" } });
    await stageForm(form, 1);

    const inFrame = screen.getByTestId("action-open-add-host");
    inFrame.focus();
    fireEvent.keyDown(inFrame, { key: "Escape" });

    expect(loc()).toBe(`/design/${IMPRINT}`);
    expect(screen.getByTestId("no-selection")).toBeTruthy();
    const rows = screen.getAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute("data-state")).toBe("pending");
    expect(document.activeElement).toBe(pencil);
  });

  it("with focus inside the frame of the URL's own subject, Escape steps out to the map, and the next one goes up", async () => {
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`);
    const inFrame = await screen.findByTestId("action-open-add-host");
    inFrame.focus();
    fireEvent.keyDown(inFrame, { key: "Escape" });
    expect(loc()).toBe(`/design/${IMPRINT}/segments/10.80.20.0-24`);
    const pane = document.querySelector(".pane-map");
    expect(document.activeElement).toBe(pane);

    fireEvent.keyDown(pane!, { key: "Escape" });
    await waitFor(() => expect(loc()).toBe(`/design/${IMPRINT}`));
  });
});

// ===========================================================================
// B4 -- a bundle with no topology export
// ===========================================================================
describe("B4: no topology export is 'not applicable yet', never a failed read", () => {
  /** `axrange/api/fixtures/design/corp-enterprise/`'s shape: a twin config and
   *  a sidecar, NO topology export -- the API answers the export with a 404. */
  const ENTERPRISE = "corp-enterprise";
  const enterprise = () =>
    server({
      id: ENTERPRISE,
      cast: {
        has_topology_export: false,
        has_provenance: true,
        segments: 4,
        observed_nodes: 0,
        observed_edges: 0,
        observed_segments: 0,
        routers: [{ name: "gw", segments: ["corp-lan", "dmz", "db", "mgmt"] }],
        segment_roster: [
          { name: "corp-lan", cidr: "10.91.10.0/24", zone_type: "vlan" },
          { name: "dmz", cidr: "10.91.20.0/28", zone_type: "vlan" },
          { name: "db", cidr: "10.91.30.0/28", zone_type: "vlan" },
          { name: "mgmt", cidr: "10.91.40.0/28", zone_type: "vlan" },
        ],
      },
      getTopology: () =>
        Promise.reject(
          new ConsoleApiError({
            kind: "not-found",
            status: 404,
            message: "no topology-export for 'corp-enterprise'",
            url: `/api/imprints/${ENTERPRISE}/topology`,
          }),
        ),
    });

  // Restated for the declared-design map (Unified Console design/16 rule 1,
  // LES-299): a bundle with no export now DRAWS its declared design and says
  // so in one line -- still never a failed read.
  it("the map draws the declared design and says there is no capture map -- neither says FAILED", async () => {
    open(`/design/${ENTERPRISE}`, enterprise().api);
    expect(await screen.findByTestId("level0-svg")).toBeTruthy();
    expect(screen.getByTestId("declared-status").textContent).toMatch(/No capture map for this bundle/);
    expect(screen.queryByTestId("graph-error")).toBeNull();
    expect(screen.queryByTestId("actions-failed")).toBeNull();
  });

  it("a host route still offers the host's actions, and they stage", async () => {
    const { api, puts } = enterprise();
    open(`/design/${ENTERPRISE}/hosts/address:10.91.10.5`, api);
    expect(await screen.findByTestId("selection-unknown-host")).toBeTruthy();
    expect(title()).toBe("Host address:10.91.10.5");
    fireEvent.click(screen.getByTestId("action-open-suppress"));
    const form = await screen.findByTestId("form-suppress");
    fireEvent.change(input(form, "gesture-note"), { target: { value: "out of scope" } });
    await stageForm(form, 1);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["suppress"]).toEqual([{ key: "address:10.91.10.5", note: "out of scope" }]);
  });
});

// ===========================================================================
// B5 + C -- the map's controls
// ===========================================================================
describe("B5 + C: the map's select and add controls", () => {
  it("are path icons -- no text glyph on the map or in the help -- with 24×24 hit targets", async () => {
    const { container } = open(`/design/${IMPRINT}`);
    const svg = await screen.findByTestId("level0-svg");
    expect(svg.textContent).not.toContain("✎");
    expect(screen.getByTestId("no-selection").textContent).not.toContain("✎");
    const pencils = [...container.querySelectorAll('[data-testid="tile-actions"]')];
    const pluses = [...container.querySelectorAll('[data-testid="site-add"]')];
    expect(pencils.length).toBeGreaterThan(0);
    expect(pluses.length).toBeGreaterThan(0);
    for (const rect of [...pencils, ...pluses]) {
      expect(rect.getAttribute("width")).toBe("24");
      expect(rect.getAttribute("height")).toBe("24");
    }
    for (const rect of pencils) {
      expect(rect.parentElement!.querySelector('[data-testid="control-icon-pencil"]')).not.toBeNull();
    }
    for (const rect of pluses) {
      expect(rect.parentElement!.querySelector('[data-testid="control-icon-plus"]')).not.toBeNull();
    }
  });

  it("the map's site + carries exactly the outline's accessible name, site by site", async () => {
    const { container } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    const outline = await screen.findAllByTestId("outline-site-add");
    expect(outline.length).toBe(3);
    for (const button of outline) {
      const site = button.getAttribute("data-site")!;
      expect(mapPlus(container, site).getAttribute("aria-label"), site).toBe(button.getAttribute("aria-label"));
    }
  });

  it("marks the selection on ONE element per view -- the tile, the outline row, the spine (`aria-current`)", async () => {
    const { container, unmount } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    const tile = () => container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    expect(tile().hasAttribute("aria-current")).toBe(false);
    fireEvent.click(tilePencil(container, "10.80.20.0/24"));
    expect(tile().getAttribute("aria-current")).toBe("true");
    expect(tile().getAttribute("class")).toMatch(/\bis-selected\b/);
    // Restated for the second fix round (LES-299): the tile carries it, and its
    // adjacent select control does NOT -- two adjacent "current" controls for
    // one segment was one too many.
    expect(tilePencil(container, "10.80.20.0/24").hasAttribute("aria-current")).toBe(false);
    expect(container.querySelectorAll('[aria-current="true"][data-pinnable]')).toHaveLength(1);
    expect(container.querySelector('[data-pinnable="segment:10.80.72.0/24"]')!.hasAttribute("aria-current")).toBe(false);
    const outline = screen.getByRole("navigation", { name: "Imprints" });
    const row = within(outline).getByRole("button", { name: /10\.80\.20\.0\/24/ });
    expect(row.getAttribute("aria-current")).toBe("true");
    unmount();

    open(`/design/${IMPRINT}/segments/10.80.20.0-24`);
    const spine = (await screen.findByTestId("spine")).parentElement!;
    expect(spine.getAttribute("aria-current")).toBe("true");
    fireEvent.click(await outlinePlus("site:10.20"));
    expect(screen.getByTestId("spine").parentElement!.hasAttribute("aria-current")).toBe(false);
  });

  it("clear returns focus to the opener -- or, when the opener is gone, to the map pane (never body)", async () => {
    const { container, unmount } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    const pencil = tilePencil(container, "10.80.20.0/24");
    pencil.focus();
    fireEvent.keyDown(pencil, { key: "Enter" });
    fireEvent.click(await screen.findByTestId("selection-clear"));
    expect(document.activeElement).toBe(pencil);
    unmount();

    // Opened from a tile the group then COLLAPSES: the opener is gone.
    const over = server({ exportData: withOverBudgetSite() });
    const second = open(`/design/${IMPRINT}?expand=site:10.80`, over.api);
    await screen.findByTestId("level0-svg");
    const hidden = tilePencil(second.container, "10.80.84.0/24");
    hidden.focus();
    fireEvent.keyDown(hidden, { key: "Enter" });
    fireEvent.click(screen.getByTestId("overflow-tile").parentElement!);
    await waitFor(() => expect(loc()).toBe(`/design/${IMPRINT}`));
    expect(hidden.isConnected).toBe(false);
    fireEvent.click(screen.getByTestId("selection-clear"));
    expect(document.activeElement).toBe(document.querySelector(".pane-map"));
  });

  it("a site's add form is isolated by its KEY: what was typed for one site never reaches another's (LES-259)", async () => {
    open(`/design/${IMPRINT}`);
    fireEvent.click(await outlinePlus("site:10.80"));
    fireEvent.change(input(liveForm(), "seg-cidr"), { target: { value: "10.80.40.0/24" } });
    fireEvent.change(input(liveForm(), "gesture-note"), { target: { value: "for 10.80" } });

    fireEvent.click(await outlinePlus("site:10.20"));
    expect(title()).toBe("Add a segment in 10.20");
    expect(input(liveForm(), "seg-cidr").value).toBe("10.20.0.0/24");
    expect(input(liveForm(), "gesture-note").value).toBe("");

    fireEvent.click(await outlinePlus("site:10.80"));
    expect(input(liveForm(), "seg-cidr").value).toBe("10.80.0.0/24");
    expect(input(liveForm(), "gesture-note").value).toBe("");
  });

  it("the host facts put the evidence badge in a cell of its own, beside the value, with the confidence", async () => {
    open(`/design/${IMPRINT}/hosts/hostname:axdc01`);
    const facts = await screen.findByTestId("host-facts");
    const role = [...facts.querySelectorAll("tr")].find((tr) => tr.querySelector("th")?.textContent === "role")!;
    const cells = role.querySelectorAll("td");
    expect(cells[0]!.textContent).toBe("domain-controller");
    expect(cells[1]!.querySelector(".ev-badge")).not.toBeNull();
  });
});
