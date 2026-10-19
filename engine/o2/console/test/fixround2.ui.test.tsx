/**
 * S5.1.a's SECOND fix round (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a,
 * iteration 7), through the real shell and the hooks:
 *
 *   - **MF1** a graph is never built from one imprint's data under another's
 *     id, and nothing of A is drawn or stageable while B loads -- A→B and
 *     catalog→B;
 *   - **SF1** a segment renamed by a saved add reads as carried under its new
 *     name, not "pruned";
 *   - **SF2** removing a segment removes the records it is the ONLY home of,
 *     after a confirmation that lists them and says what leaving them would
 *     do; an add of an observed segment goes alone; an orphaned record is
 *     flagged;
 *   - **SF3** an apply on a bundle with no topology export keeps the host's
 *     actions and what was typed in them;
 *   - **T5** Escape never leaves an imprint with unsaved edits;
 *   - **ZT** a new segment's zone type starts at the site's dominant declared
 *     zone, else the cast's, else `simple`;
 *   - the nits: Escape on the URL's own subject, `body` as no opener, a
 *     re-staged add replacing visibly, a stale conduit peer.
 *
 * The fakes are STATEFUL where a re-read is the question, and can hold a read
 * open: a fake that answers at once cannot show what a render sees while a
 * request is in flight.
 */
import type { ReactNode } from "react";
import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import { ApiContext } from "../src/api/context";
import { useCast } from "../src/api/hooks";
import type {
  ApplyResponse,
  AssertionsDoc,
  CastSummary,
  ImprintDiff,
  ImprintList,
  ImprintProvenance,
  TopologyExport,
} from "../src/api/types";
import { ConsoleRoutes, TestApp } from "../src/routes";
import type { ConsoleGraph } from "../src/model/graph";
import { useGraph } from "../src/views/useGraph";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const A = "imp-a";
const B = "imp-b";
const AS_OF = "2026-09-08T00:00:00Z";
const SOURCE = "operator: tester";

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

const never = <T,>(): Promise<T> => new Promise<T>(() => {});

interface ServerOpts {
  /** Imprints whose EVERY read never resolves. */
  stalled?: readonly string[];
  file?: Partial<AssertionsDoc>;
  cast?: Partial<CastSummary>;
  getTopology?: ConsoleApi["getTopology"];
  /** Replace the cast read after the first (an apply's re-read). */
  castReRead?: (id: string) => Promise<CastSummary>;
  changed?: boolean;
}

function server(opts: ServerOpts = {}) {
  const stalled = new Set(opts.stalled ?? []);
  const files = new Map<string, AssertionsDoc>();
  const fileOf = (id: string): AssertionsDoc => files.get(id) ?? fileDoc(id, opts.file);
  let writes = 1;
  const puts: Array<Record<string, unknown>> = [];
  const applies: unknown[] = [];
  let casts = 0;
  const write = (id: string, document: Record<string, unknown>): AssertionsDoc => {
    writes += 1;
    const doc = fileDoc(id, {
      etag: `etag-${writes}`,
      add: (document["add"] as AssertionsDoc["add"]) ?? [],
      override: (document["override"] as AssertionsDoc["override"]) ?? [],
      suppress: (document["suppress"] as AssertionsDoc["suppress"]) ?? [],
    });
    files.set(id, doc);
    return doc;
  };
  const provenance = (id: string): ImprintProvenance => ({
    imprint_id: id,
    rows: [],
    sidecar_shape: "absent",
    sidecar_schema: null,
    evidence_source: "none",
    code_fingerprint: null,
    population: {},
    warnings: [],
  });
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const api: ConsoleApi = {
    listImprints: () =>
      Promise.resolve({
        imprints: [A, B].map((id) => ({ id, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" })),
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: (id) => (stalled.has(id) ? never() : Promise.resolve(provenance(id))),
    getTopology: (id, signal) =>
      stalled.has(id) ? never() : opts.getTopology ? opts.getTopology(id, signal) : Promise.resolve(trimmedExport as TopologyExport),
    getCast: (id) => {
      if (stalled.has(id)) return never();
      casts += 1;
      if (casts > 1 && opts.castReRead) return opts.castReRead(id);
      return Promise.resolve(castFor(id, opts.cast));
    },
    getAssertions: (id) => (stalled.has(id) ? never() : Promise.resolve(fileOf(id))),
    putAssertions: (id, document) => {
      puts.push(document);
      return Promise.resolve(write(id, document));
    },
    getLayout: (id) =>
      stalled.has(id)
        ? never()
        : Promise.resolve({ imprint_id: id, path: "", exists: false, etag: null, schema: "axr-layout/1", imprint: id, pins: {}, plane: null }),
    putLayout: unused("putLayout"),
    apply: (id, req) => {
      const document = req.document as Record<string, unknown>;
      applies.push(document);
      const assertions = write(id, document);
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
    getDiff: (id) =>
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
  return { api, puts, applies, castCount: () => casts };
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
const input = (scope: HTMLElement, id: string): HTMLInputElement => within(scope).getByTestId(id) as HTMLInputElement;
const tilePencil = (c: HTMLElement, cidr: string): HTMLElement => {
  const rect = c.querySelector(`[data-testid="tile-actions"][data-cidr="${cidr}"]`);
  if (!rect?.parentElement) throw new Error(`no select control for ${cidr}`);
  return rect.parentElement;
};
async function outlinePlus(site: string): Promise<HTMLElement> {
  const adds = await screen.findAllByTestId("outline-site-add");
  const button = adds.find((b) => b.getAttribute("data-site") === site);
  if (!button) throw new Error(`no outline + for ${site}`);
  return button;
}
function fact(table: HTMLElement, label: string): string {
  const row = [...table.querySelectorAll("tr")].find((tr) => tr.querySelector("th")?.textContent === label);
  if (!row) throw new Error(`no fact row ${label}`);
  return row.querySelectorAll("td")[0]?.textContent ?? "";
}
async function stage(form: HTMLElement, rows: number): Promise<void> {
  fireEvent.submit(form);
  await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(rows));
}

// ===========================================================================
// MF1 -- one imprint's data is never another's
// ===========================================================================
describe("MF1: a resource answers only for its own subject", () => {
  function wrapperFor(api: ConsoleApi) {
    return ({ children }: { children: ReactNode }) => <ApiContext.Provider value={api}>{children}</ApiContext.Provider>;
  }

  it("useCast returns NOTHING of imprint A on the very first render for imprint B", async () => {
    const { api } = server({ stalled: [B] });
    const seen: Array<{ id: string | null; castId: string | null }> = [];
    const { rerender } = renderHook(
      ({ id }: { id: string | null }) => {
        const cast = useCast(id);
        seen.push({ id, castId: cast.data?.id ?? null });
        return cast;
      },
      { wrapper: wrapperFor(api), initialProps: { id: A as string | null } },
    );
    await waitFor(() => expect(seen.at(-1)?.castId).toBe(A));
    rerender({ id: B });
    await act(async () => {});
    const forB = seen.filter((r) => r.id === B);
    expect(forB.length).toBeGreaterThan(0);
    expect(forB.every((r) => r.castId === null)).toBe(true);
  });

  it("useGraph builds NO graph under B's id -- on any render -- while B's reads never resolve (A→B and via the catalog)", async () => {
    for (const path of [[A, B], [A, null, B]] as Array<Array<string | null>>) {
      const { api } = server({ stalled: [B] });
      const seen: Array<{ id: string | null; graph: ConsoleGraph | null }> = [];
      const { rerender, unmount } = renderHook(
        ({ id }: { id: string | null }) => {
          const resource = useGraph(id);
          seen.push({ id, graph: resource.graph });
          return resource;
        },
        { wrapper: wrapperFor(api), initialProps: { id: path[0] ?? null } },
      );
      await waitFor(() => expect(seen.at(-1)?.graph?.imprintId).toBe(A));
      for (const id of path.slice(1)) {
        rerender({ id });
        await act(async () => {});
      }
      const forB = seen.filter((r) => r.id === B);
      expect(forB.length, path.join("→")).toBeGreaterThan(0);
      expect(forB.every((r) => r.graph === null), path.join("→")).toBe(true);
      unmount();
    }
  });

  it("switching A→B in the shell draws nothing of A and offers nothing to stage while B loads", async () => {
    const { api } = server({ stalled: [B] });
    const { container } = open(`/design/${A}`, api);
    await screen.findByTestId("level0-svg");
    expect(container.querySelectorAll('[data-testid="segment-tile"]').length).toBeGreaterThan(0);

    const outline = screen.getByRole("navigation", { name: "Imprints" });
    fireEvent.click(within(outline).getByRole("link", { name: new RegExp(`^${B}`) }));
    await waitFor(() => expect(loc()).toBe(`/design/${B}`));

    expectNothingOfA(container);
  });

  it("switching catalog→B draws nothing of A (a disabled read kept A's data) and offers nothing to stage", async () => {
    const { api } = server({ stalled: [B] });
    const { container } = open(`/design/${A}`, api);
    await screen.findByTestId("level0-svg");
    fireEvent.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "imprints" }));
    await waitFor(() => expect(loc()).toBe("/design"));
    const outline = screen.getByRole("navigation", { name: "Imprints" });
    fireEvent.click(within(outline).getByRole("link", { name: new RegExp(`^${B}`) }));
    await waitFor(() => expect(loc()).toBe(`/design/${B}`));

    expectNothingOfA(container);
  });

  function expectNothingOfA(container: HTMLElement): void {
    expect(screen.getByTestId("graph-loading")).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="segment-tile"]')).toHaveLength(0);
    expect(container.querySelectorAll('[data-testid="tile-actions"]')).toHaveLength(0);
    // The outline tree needs a graph: none of A's sites or segments.
    expect(screen.queryByTestId("outline-tree")).toBeNull();
    expect(screen.queryAllByTestId("outline-site-add")).toHaveLength(0);
    // The inspector says it is loading, and offers no action at all.
    expect(screen.getByTestId("actions-loading")).toBeTruthy();
    expect(screen.queryAllByTestId(/^action-open-/)).toHaveLength(0);
    expect(screen.queryByTestId("change-row")).toBeNull();
  }
});

// ===========================================================================
// SF1 -- a rename on record
// ===========================================================================
describe("SF1: a segment renamed by a saved add is carried under its new name, not called pruned", () => {
  const renamed = [...ROSTER, { name: "ot-lab", cidr: "10.200.9.0/24", zone_type: "vlan" }];
  const file = { add: [{ kind: "segment", key: "10.80.72.0/24", name: "ot-lab", note: "rename" }] };

  it("its facts say carried AS the new name, with that entry's CIDR and zone", async () => {
    open(`/design/${A}/segments/10.80.72.0-24`, server({ cast: { segment_roster: renamed }, file }).api);
    const facts = await screen.findByTestId("segment-facts");
    expect(fact(facts, "declared in the cast")).toBe("yes, as ot-lab — renamed by a saved add in this file");
    expect(fact(facts, "declared as")).toBe("10.200.9.0/24");
    expect(fact(facts, "zone")).toBe("vlan");
  });

  it("re-adding it is refused as DECLARED (as ot-lab), and its new name stays its own", async () => {
    open(`/design/${A}`, server({ cast: { segment_roster: renamed }, file }).api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.72.0/24" } });
    expect(within(form).getByTestId("seg-claim").textContent).toBe(
      "10.80.72.0/24 is already declared in the cast (as ot-lab) — select it and use Add host here. Stage refuses it.",
    );
    // Another network may not take the cast's name for it.
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.50.0/24" } });
    fireEvent.change(input(form, "seg-name"), { target: { value: "ot-lab" } });
    expect(within(form).getByTestId("seg-name-claim").textContent).toMatch(
      /^the name ot-lab is already used by 10\.80\.72\.0\/24, a segment declared in the cast/,
    );
  });
});

// ===========================================================================
// SF2 -- a segment's dependents
// ===========================================================================
describe("SF2: removing a segment removes what it is the only home of -- listed first -- and orphans are flagged", () => {
  it("lists the dependents, keeps everything on 'keep them', removes all together on confirm (pending: no PUT)", async () => {
    const { api, puts } = server();
    open(`/design/${A}`, api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.31.0/24" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "the OT lab" } });
    await stage(form, 1);
    fireEvent.click(screen.getByTestId("action-open-add-host"));
    const host = await screen.findByTestId("form-add-host");
    fireEvent.change(input(host, "host-key"), { target: { value: "address:10.80.31.10" } });
    fireEvent.change(input(host, "gesture-note"), { target: { value: "a PLC" } });
    await stage(host, 2);
    fireEvent.click(screen.getByTestId("action-open-add-conduit"));
    const conduit = await screen.findByTestId("form-add-conduit");
    fireEvent.change(within(conduit).getByTestId("conduit-b"), { target: { value: "10.80.20.0/24" } });
    fireEvent.change(input(conduit, "gesture-note"), { target: { value: "reaches the DC" } });
    await stage(conduit, 3);

    const segmentRow = () => screen.getAllByTestId("change-row").find((r) => r.textContent?.includes("+ segment 10.80.31.0/24"))!;
    fireEvent.click(within(segmentRow()).getByTestId("change-remove"));
    const confirm = screen.getByTestId("remove-confirm");
    const listed = within(confirm).getByTestId("remove-confirm-list").textContent ?? "";
    expect(listed).toContain("+ host address:10.80.31.10");
    expect(listed).toContain("+ conduit 10.80.20.0/24|10.80.31.0/24");
    // What leaving them would do, in the compiler's terms: the host fails the
    // apply, the conduit is reported ineffective -- not "the apply would fail".
    expect(within(confirm).getAllByTestId("remove-confirm-consequence").map((p) => p.textContent)).toEqual([
      "left without it, the host would fail the apply — the compiler refuses a host whose segment is neither observed nor added by this file",
      "left without it, the conduit would be reported ineffective and dropped — the compiler does not emit a conduit to a segment the twin does not carry",
    ]);
    expect(within(confirm).getByTestId("remove-confirm-all").textContent).toBe("remove all 3");

    fireEvent.click(within(confirm).getByTestId("remove-confirm-cancel"));
    expect(screen.queryByTestId("remove-confirm")).toBeNull();
    expect(screen.getAllByTestId("change-row")).toHaveLength(3);

    fireEvent.click(within(segmentRow()).getByTestId("change-remove"));
    fireEvent.click(within(screen.getByTestId("remove-confirm")).getByTestId("remove-confirm-all"));
    await waitFor(() => expect(screen.queryAllByTestId("change-row")).toHaveLength(0));
    expect(puts).toHaveLength(0);
  });

  it("a SAVED segment and its saved host go in ONE re-save of the file", async () => {
    const { api, puts } = server({
      file: {
        add: [
          { kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31-0", note: "the OT lab" },
          { kind: "host", key: "address:10.80.31.10", segment: "10.80.31.0/24", note: "a PLC" },
          { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", note: "unrelated" },
        ],
      },
    });
    open(`/design/${A}`, api);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(3));
    const segmentRow = screen.getAllByTestId("change-row").find((r) => r.textContent?.includes("+ segment 10.80.31.0/24"))!;
    fireEvent.click(within(segmentRow).getByTestId("change-remove"));
    fireEvent.click(within(screen.getByTestId("remove-confirm")).getByTestId("remove-confirm-all"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([
      { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", note: "unrelated" },
    ]);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
  });

  it("an add of an OBSERVED segment goes ALONE -- the host on it keeps its home, so nothing asks and nothing else goes", async () => {
    // The reviewer's case (round 3): a saved zone assertion on the observed
    // 10.80.20.0/24 and a saved host on it. The compiler resolves the host
    // against the observed segment, so the add is not its only home.
    const { api, puts } = server({
      file: {
        add: [
          { kind: "segment", key: "10.80.20.0/24", name: "seg-10-80-20-0", zone_type: "vlan", note: "a tagged VLAN" },
          { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", role: "file-server", note: "a host on the observed segment" },
        ],
      },
    });
    open(`/design/${A}`, api);
    await screen.findAllByTestId("outline-site-add"); // the topology is in: the observed set is known
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(2));
    const row = (text: string) => screen.getAllByTestId("change-row").find((r) => r.textContent?.includes(text))!;
    expect(within(row("+ host address:10.80.20.99")).queryByTestId("change-orphan")).toBeNull();

    fireEvent.click(within(row("+ segment 10.80.20.0/24")).getByTestId("change-remove"));
    expect(screen.queryByTestId("remove-confirm")).toBeNull();
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([
      { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", role: "file-server", note: "a host on the observed segment" },
    ]);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(1));
    expect(within(row("+ host address:10.80.20.99")).queryByTestId("change-orphan")).toBeNull();
  });

  it("flags a record whose segment has no home -- not observed, not added; the cast roster is none -- with the reason", async () => {
    // Restated (LES-299): the installed cast's roster is not a home -- the
    // compiler resolves a host against the observed segments and this file's
    // adds only, and a roster CIDR is a relocated, deployed address -- so a host
    // on 10.200.3.0/24 (seg-10-80-20-0's deployed CIDR) is flagged too.
    open(
      `/design/${A}`,
      server({
        cast: { segment_roster: ROSTER },
        file: {
          add: [
            { kind: "host", key: "address:10.80.99.5", segment: "10.80.99.0/24", note: "its segment was removed" },
            { kind: "host", key: "address:10.80.20.99", segment: "10.80.20.0/24", note: "fine" },
            { kind: "conduit", key: "10.80.20.0/24|10.80.99.0/24", note: "half gone" },
            { kind: "host", key: "address:10.200.3.7", segment: "10.200.3.0/24", note: "on a deployed CIDR" },
          ],
        },
      }).api,
    );
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(4));
    await screen.findAllByTestId("outline-site-add");
    const rows = screen.getAllByTestId("change-row");
    const flag = (text: string) => within(rows.find((r) => r.textContent?.includes(text))!).queryByTestId("change-orphan")?.textContent ?? null;
    expect(flag("+ host address:10.80.99.5")).toBe(
      "orphaned: 10.80.99.0/24 is neither an observed segment nor one this file adds — the compiler refuses a host on it",
    );
    expect(flag("+ host address:10.80.20.99")).toBeNull();
    expect(flag("+ conduit 10.80.20.0/24|10.80.99.0/24")).toBe(
      "orphaned: 10.80.99.0/24 is neither an observed segment nor one this file adds — the compiler reports the conduit ineffective and drops it",
    );
    expect(flag("+ host address:10.200.3.7")).toBe(
      "orphaned: 10.200.3.0/24 is neither an observed segment nor one this file adds — the compiler refuses a host on it",
    );
  });
});

// ===========================================================================
// SF3 -- no topology export, and an apply
// ===========================================================================
describe("SF3: on a bundle with no topology export, an apply keeps the host's actions and what was typed", () => {
  // Restated for the declared-design map (Unified Console, LES-299): a bundle
  // with no export now DRAWS its declared design, so the host route is a host
  // the drawing does not carry ("selection-unknown-host"), not "no export".
  // The bundle carries a sidecar, so it is NOT hand-authored and its host
  // actions are offered (a hand-authored twin's are covered in declared.ui).
  it("while the cast is re-read the export is still 'absent' -- the frame and its typed value never unmount", async () => {
    let release: () => void = () => {
      throw new Error("the cast was never re-read");
    };
    const notFound = new ConsoleApiError({ kind: "not-found", status: 404, message: "no topology-export", url: "/api" });
    const { api, applies, castCount } = server({
      changed: true,
      cast: {
        has_topology_export: false,
        has_provenance: true,
        segment_roster: [{ name: "corp-lan", cidr: "10.91.10.0/24", zone_type: "vlan" }],
      },
      getTopology: () => Promise.reject(notFound),
      castReRead: (id) =>
        new Promise<CastSummary>((resolve) => {
          release = () =>
            resolve(
              castFor(id, {
                has_topology_export: false,
                has_provenance: true,
                segment_roster: [{ name: "corp-lan", cidr: "10.91.10.0/24", zone_type: "vlan" }],
              }),
            );
        }),
    });
    open(`/design/${A}/hosts/address:10.91.10.5`, api);
    expect(await screen.findByTestId("selection-unknown-host")).toBeTruthy();
    fireEvent.click(screen.getByTestId("action-open-override"));
    const image = await screen.findByTestId("form-override-image");
    fireEvent.change(input(image, "image-value"), { target: { value: "debian-12" } });

    fireEvent.click(screen.getByTestId("apply-button"));
    await waitFor(() => expect(applies).toHaveLength(1));
    await waitFor(() => expect(castCount()).toBe(2));
    // The re-read is in flight: the declared drawing is HELD, never a failed
    // read or a load, and the frame with its typed value stays.
    expect(screen.getByTestId("selection-unknown-host")).toBeTruthy();
    expect(screen.queryByTestId("actions-failed")).toBeNull();
    expect(screen.queryByTestId("actions-loading")).toBeNull();
    expect(screen.queryByTestId("graph-error")).toBeNull();
    expect(screen.queryByTestId("graph-loading")).toBeNull();
    expect(input(screen.getByTestId("form-override-image"), "image-value").value).toBe("debian-12");

    release();
    await waitFor(() => expect(loc()).toBe(`/design/${A}/hosts/address:10.91.10.5?changed=1`));
    expect(input(screen.getByTestId("form-override-image"), "image-value").value).toBe("debian-12");
  });
});

// ===========================================================================
// T5 -- Escape does not discard unsaved work
// ===========================================================================
describe("T5: Escape never leaves an imprint with unsaved edits", () => {
  async function stagePending(container: HTMLElement): Promise<void> {
    await screen.findByTestId("level0-svg");
    const pencil = tilePencil(container, "10.80.20.0/24");
    pencil.focus();
    fireEvent.keyDown(pencil, { key: "Enter" });
    for (const [n, key] of [[1, "address:10.80.20.97"], [2, "address:10.80.20.98"], [3, "address:10.80.20.99"]] as const) {
      fireEvent.click(screen.getByTestId("action-open-add-host"));
      const form = await screen.findByTestId("form-add-host");
      fireEvent.change(input(form, "host-key"), { target: { value: key } });
      fireEvent.change(input(form, "gesture-note"), { target: { value: "pending" } });
      await stage(form, n);
      fireEvent.click(screen.getByTestId("action-open-add-host"));
    }
  }

  it("with pending edits, the second Escape stays on the imprint, keeps every row, and SAYS why in a live region", async () => {
    const { container } = open(`/design/${A}`, server().api);
    await stagePending(container);
    const status = screen.getByTestId("inspector-status");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.textContent).toBe("");

    fireEvent.keyDown(document.body, { key: "Escape" }); // clears the selection
    expect(screen.getByTestId("no-selection")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" }); // would have left for the catalog
    await act(async () => {});
    expect(loc()).toBe(`/design/${A}`);
    expect(screen.getAllByTestId("change-row")).toHaveLength(3);
    expect(screen.getAllByTestId("change-row").every((r) => r.getAttribute("data-state") === "pending")).toBe(true);
    expect(screen.getByTestId("inspector-status").textContent).toBe(
      "3 unsaved edits — save or discard them before leaving this imprint. Escape does not discard unsaved work.",
    );
  });

  it("once the edits are SAVED -- nothing unsaved -- Escape leaves as before", async () => {
    const { api, puts } = server();
    const { container } = open(`/design/${A}`, api);
    await stagePending(container);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getAllByTestId("change-row").every((r) => r.getAttribute("data-state") === "saved")).toBe(true),
    );
    fireEvent.keyDown(document.body, { key: "Escape" });
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(loc()).toBe("/design"));
  });
});

// ===========================================================================
// ZT -- the zone type a new segment starts at
// ===========================================================================
describe("ZT: a new segment's zone type starts at what its site's cast segments declare", () => {
  it("per site, then twin-wide, then simple with a note -- and the operator's choice is kept", async () => {
    const zoneOf = () => (within(screen.getByTestId("form-add-segment")).getByTestId("seg-zone") as HTMLSelectElement).value;
    const note = () => screen.getByTestId("seg-zone-default").textContent;

    const { unmount } = open(`/design/${A}`, server({ cast: { segment_roster: ROSTER } }).api);
    fireEvent.click(await outlinePlus("site:10.80"));
    expect(zoneOf()).toBe("simple");
    expect(note()).toBe("starts at simple — the zone type most of 10.80's cast segments declare");
    fireEvent.click(await outlinePlus("site:10.20"));
    // 10.20's declared zones: one null, one vlan.
    expect(zoneOf()).toBe("vlan");
    fireEvent.click(screen.getByTestId("outline-add-segment"));
    // No site: the cast's own dominant zone (simple ×4 against vlan ×1).
    expect(zoneOf()).toBe("simple");
    expect(note()).toBe("starts at simple — the zone type most of the cast's segments declare");
    // A choice replaces the default, and the default's note goes with it.
    fireEvent.change(within(screen.getByTestId("form-add-segment")).getByTestId("seg-zone"), { target: { value: "vlan" } });
    expect(zoneOf()).toBe("vlan");
    expect(screen.queryByTestId("seg-zone-default")).toBeNull();
    unmount();

    // Restated (LES-299): with no zone published anywhere, the form starts at
    // `simple`, the twin config's own default -- never `vlan`, which one segment
    // makes the whole twin's zone type, and a VLAN zone rejects SNAT egress.
    open(`/design/${A}`, server().api);
    fireEvent.click(await outlinePlus("site:10.80"));
    expect(zoneOf()).toBe("simple");
    expect(note()).toBe("starts at simple, the twin config's own default — no zone type is published for this twin's segments");
  });
});

// ===========================================================================
// the nits
// ===========================================================================
describe("the second fix round's nits", () => {
  it("Escape on a selection EQUAL to the URL's subject spends no press on it -- one Escape goes up", async () => {
    open(`/design/${A}/segments/10.80.20.0-24`, server().api);
    const spine = (await screen.findByTestId("spine")).parentElement!;
    spine.focus();
    fireEvent.keyDown(spine, { key: "Enter" }); // explicit, but the same subject
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(loc()).toBe(`/design/${A}`));
  });

  it("a selection made with focus on `body` has no opener: clear hands focus to the map pane, not back to body", async () => {
    const { container } = open(`/design/${A}`, server().api);
    await screen.findByTestId("level0-svg");
    expect(document.activeElement).toBe(document.body);
    fireEvent.click(tilePencil(container, "10.80.20.0/24")); // a pointer click moves no focus in jsdom
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.click(screen.getByTestId("selection-clear"));
    expect(document.activeElement).toBe(document.querySelector(".pane-map"));
  });

  it("re-staging a host add under the same key says it REPLACES the pending one, and the change list keeps one row", async () => {
    open(`/design/${A}/segments/10.80.20.0-24`, server().api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    let form = await screen.findByTestId("form-add-host");
    fireEvent.change(input(form, "host-key"), { target: { value: "address:10.80.20.99" } });
    expect(within(form).queryByTestId("replaces-note")).toBeNull();
    fireEvent.change(input(form, "gesture-note"), { target: { value: "first" } });
    await stage(form, 1);

    form = screen.getByTestId("form-add-host");
    fireEvent.change(input(form, "host-key"), { target: { value: "address:10.80.20.99" } });
    expect(within(form).getByTestId("replaces-note").textContent).toBe(
      "a pending add for address:10.80.20.99 is already in this file — Stage replaces it, and the change list keeps one row.",
    );
    fireEvent.change(input(form, "host-role"), { target: { value: "file-server" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "second" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")[0]!.textContent).toContain("as file-server"));
    expect(screen.getAllByTestId("change-row")).toHaveLength(1);
  });

  it("a conduit peer that has since gone is not staged -- the picker falls back to a peer still on offer", async () => {
    const { api, puts } = server();
    open(`/design/${A}`, api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    fireEvent.change(input(form, "seg-cidr"), { target: { value: "10.80.31.0/24" } });
    fireEvent.change(input(form, "gesture-note"), { target: { value: "added" } });
    await stage(form, 1);

    // An OBSERVED segment's conduit form, pointed at the added one...
    fireEvent.click(within(screen.getByRole("navigation", { name: "Imprints" })).getByRole("button", { name: /^10\.80\.20\.0\/24/ }));
    fireEvent.click(await screen.findByTestId("action-open-add-conduit"));
    const conduit = await screen.findByTestId("form-add-conduit");
    fireEvent.change(within(conduit).getByTestId("conduit-b"), { target: { value: "10.80.31.0/24" } });
    expect((within(conduit).getByTestId("conduit-b") as HTMLSelectElement).value).toBe("10.80.31.0/24");
    // ...whose added segment is then removed.
    fireEvent.click(within(screen.getAllByTestId("change-row")[0]!).getByTestId("change-remove"));
    await waitFor(() => expect(screen.queryAllByTestId("change-row")).toHaveLength(0));
    const select = within(screen.getByTestId("form-add-conduit")).getByTestId("conduit-b") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).not.toContain("10.80.31.0/24");
    expect(select.value).toBe("10.1.10.0/24");

    fireEvent.change(input(screen.getByTestId("form-add-conduit"), "gesture-note"), { target: { value: "the fallback" } });
    await stage(screen.getByTestId("form-add-conduit"), 1);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([{ kind: "conduit", key: "10.1.10.0/24|10.80.20.0/24", note: "the fallback" }]);
  });
});
