/**
 * S5.1.a through the real shell -- the gaps `edit.ui.test.tsx` leaves
 * (`docs/WORK_ORDER_console_canvas.md` §3 S5.1.a, iteration-7 tester):
 *
 *   - **lifetime**: a route change drops an in-place selection and returning
 *     does not resurrect it; EVERY view flag the map has a control for keeps
 *     it and what was typed under it (the writer's test covers `expand`);
 *   - **keyboard**: Enter AND Space on every SVG affordance (and no other
 *     key), the outline's `+` buttons moving focus into what they opened, the
 *     action accordion's state, and no focus theft while staging under a
 *     selection the URL makes -- which, unlike an explicit one, is a new object
 *     on every render;
 *   - **claims across save and apply**: an add stays claimed after its file is
 *     re-read by an apply;
 *   - **records**: all seven gestures, reached ONLY through the new paths, write
 *     exactly the document the S5 record layer builds from the same values;
 *   - **branches no test reached**: an unknown host key, a CIDR nobody holds,
 *     the two `unavailable` reasons, a FULL prefix, the map's misc `+`.
 *
 * The fake API is STATEFUL (a PUT or an apply writes the file a later GET
 * reads), because "what happens after an apply reload" is a question about what
 * the re-read returns, and a fake that always returns the seed file answers a
 * different one.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { describe, expect, it } from "vitest";

import type { ConsoleApi } from "../src/api/client";
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
import { buildRecord, type Gesture } from "../src/edit/records";
import { documentFrom, editReducer, initialEditState } from "../src/edit/store";
import { buildGraph } from "../src/model/graph";
import { ElementActions } from "../src/views/ElementActions";
import type { Selection } from "../src/views/selection";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

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

const AS_OF = "2026-09-08T00:00:00Z";
const SOURCE = "operator: tester";

function fileDoc(overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/${IMPRINT}.assertions.yaml`,
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: IMPRINT,
    as_of: AS_OF,
    source: SOURCE,
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

interface Server {
  api: ConsoleApi;
  puts: Array<Record<string, unknown>>;
  applies: Array<Record<string, unknown>>;
}

/**
 * A fake with a FILE: every PUT and every apply replaces it (with a fresh
 * etag), and every GET returns what is on it now -- the property a re-read
 * after an apply actually depends on.
 */
function server(opts: { file?: AssertionsDoc; changed?: boolean; exportData?: TopologyExport } = {}): Server {
  let file = opts.file ?? fileDoc();
  let writes = 1;
  const puts: Server["puts"] = [];
  const applies: Server["applies"] = [];
  const write = (document: Record<string, unknown>): AssertionsDoc => {
    writes += 1;
    file = fileDoc({
      etag: `etag-${writes}`,
      add: (document["add"] as AssertionsDoc["add"]) ?? [],
      override: (document["override"] as AssertionsDoc["override"]) ?? [],
      suppress: (document["suppress"] as AssertionsDoc["suppress"]) ?? [],
    });
    return file;
  };
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const api: ConsoleApi = {
    listImprints: () =>
      Promise.resolve({
        imprints: [{ id: IMPRINT, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" }],
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROVENANCE),
    getTopology: () => Promise.resolve(opts.exportData ?? (trimmedExport as TopologyExport)),
    getCast: (id) => Promise.resolve({ ...CAST, id, twin_config_id: id }),
    getAssertions: () => Promise.resolve(file),
    putAssertions: (_id, document) => {
      puts.push(document);
      return Promise.resolve(write(document));
    },
    getLayout: () =>
      Promise.resolve({ imprint_id: IMPRINT, path: "", exists: false, etag: null, schema: "axr-layout/1", imprint: IMPRINT, pins: {}, plane: null }),
    putLayout: unused("putLayout"),
    apply: (_id, req) => {
      const document = req.document as Record<string, unknown>;
      applies.push(document);
      const assertions = write(document);
      return Promise.resolve({
        imprint_id: IMPRINT,
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
        imprint_id: IMPRINT,
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

/** The `Nav` group around a drawn affordance -- it carries the role, the tab
 *  stop and the handlers; the rect inside it carries the test id. */
function navOf(container: HTMLElement, selector: string): HTMLElement {
  const rect = container.querySelector(selector);
  if (!rect?.parentElement) throw new Error(`nothing matches ${selector}`);
  return rect.parentElement;
}
const tilePencil = (c: HTMLElement, cidr: string): HTMLElement =>
  navOf(c, `[data-testid="tile-actions"][data-cidr="${cidr}"]`);
const mapPlus = (c: HTMLElement, site: string): HTMLElement =>
  navOf(c, `[data-testid="site-add"][data-site="${site}"]`);

async function outlinePlus(site: string): Promise<HTMLElement> {
  const adds = await screen.findAllByTestId("outline-site-add");
  const button = adds.find((b) => b.getAttribute("data-site") === site);
  if (!button) throw new Error(`no outline + for ${site}`);
  return button;
}

const selectionKind = (): string | null =>
  screen.queryByTestId("element-actions")?.getAttribute("data-selection") ?? null;
const title = (): string | null => screen.queryByTestId("selection-title")?.textContent ?? null;
const field = (form: HTMLElement, id: string): HTMLInputElement =>
  within(form).getByTestId(id) as HTMLInputElement;

// ---------------------------------------------------------------------------
// lifetime
// ---------------------------------------------------------------------------
describe("S5.1.a: the selection's lifetime", () => {
  it("a route change drops an in-place selection, and coming back does not resurrect it", async () => {
    const { container } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    fireEvent.click(tilePencil(container, "10.80.20.0/24"));
    expect(title()).toBe("Segment 10.80.20.0/24");

    // Drill into a DIFFERENT segment: the URL's subject replaces the explicit one.
    fireEvent.click(container.querySelector('[data-pinnable="segment:10.80.72.0/24"]')!);
    await screen.findByTestId("level1-svg");
    await waitFor(() => expect(title()).toBe("Segment 10.80.72.0/24"));
    // It is the URL's selection now, so there is nothing to clear.
    expect(screen.queryByTestId("selection-clear")).toBeNull();

    // Back up: the tile selected before the drill is NOT brought back.
    fireEvent.keyDown(window, { key: "Escape" });
    await screen.findByTestId("level0-svg");
    await waitFor(() => expect(loc()).toBe(`/design/${IMPRINT}`));
    expect(screen.getByTestId("no-selection")).toBeTruthy();
    expect(selectionKind()).toBeNull();
  });

  it("every VIEW flag the map has a control for keeps the site + form AND what was typed in it", async () => {
    // One row per control the operator can actually press. `expand` is the
    // writer's own test (edit.ui.test.tsx); these are the other three VIEW
    // flags (`model/zoom.ts`'s taxonomy). Since the fix round the selection is
    // keyed on the pathname alone, so dimming filters and `?changed=1` are
    // covered too (`fixround.ui.test.tsx`, B2). Each row is a fresh render, so a
    // row cannot pass on state an earlier one left behind.
    const rows: Array<{ flag: string; start: string; press: () => HTMLElement; after: string }> = [
      {
        flag: "summary",
        start: `/design/${IMPRINT}`,
        press: () => screen.getByTestId("summary-toggle"),
        after: `/design/${IMPRINT}?summary=1`,
      },
      {
        flag: "order",
        start: `/design/${IMPRINT}`,
        press: () => screen.getByTestId("order-cidr"),
        after: `/design/${IMPRINT}?order=cidr`,
      },
      {
        // `show all` is offered only while something is hidden or `?ew=all` is
        // already on, so the row starts from `?ew=all` and presses the way back.
        flag: "ew",
        start: `/design/${IMPRINT}?ew=all`,
        press: () => screen.getByTestId("ew-toggle"),
        after: `/design/${IMPRINT}`,
      },
    ];
    for (const row of rows) {
      const { container, unmount } = open(row.start);
      await screen.findByTestId("level0-svg");
      fireEvent.click(mapPlus(container, "site:10.80"));
      const form = await screen.findByTestId("form-add-segment");
      fireEvent.change(field(form, "seg-cidr"), { target: { value: "10.80.40.0/24" } });
      fireEvent.change(field(form, "gesture-note"), { target: { value: "typed before the view changed" } });

      fireEvent.click(row.press());
      await waitFor(() => expect(loc(), row.flag).toBe(row.after));

      expect(selectionKind(), row.flag).toBe("site-add");
      const live = screen.getByTestId("form-add-segment");
      expect(field(live, "seg-cidr").value, row.flag).toBe("10.80.40.0/24");
      expect(field(live, "gesture-note").value, row.flag).toBe("typed before the view changed");
      unmount();
    }
  });
});

// ---------------------------------------------------------------------------
// keyboard
// ---------------------------------------------------------------------------
describe("S5.1.a: keyboard", () => {
  interface Affordance {
    name: string;
    url: string;
    /** Put the page in the state BEFORE the gesture; returns the selection
     *  kind that state has, so a no-op key can be seen to change nothing. */
    arrange: (c: HTMLElement) => Promise<string | null>;
    target: (c: HTMLElement) => HTMLElement;
    expectKind: string;
  }
  const affordances: Affordance[] = [
    {
      name: "a tile's ✎",
      url: `/design/${IMPRINT}`,
      arrange: async () => {
        await screen.findByTestId("level0-svg");
        return null;
      },
      target: (c) => tilePencil(c, "10.80.20.0/24"),
      expectKind: "segment",
    },
    {
      name: "a map site header's +",
      url: `/design/${IMPRINT}`,
      arrange: async () => {
        await screen.findByTestId("level0-svg");
        return null;
      },
      target: (c) => mapPlus(c, "site:10.20"),
      expectKind: "site-add",
    },
    {
      name: "the level-1 spine",
      url: `/design/${IMPRINT}/segments/10.80.20.0-24`,
      // The URL already selects the segment; move the one slot elsewhere
      // first, or selecting the spine could not be told apart from nothing.
      arrange: async () => {
        fireEvent.click(await outlinePlus("site:10.20"));
        await screen.findByTestId("form-add-segment");
        return "site-add";
      },
      target: () => screen.getByTestId("spine").parentElement!,
      expectKind: "segment",
    },
  ];

  it("Enter AND Space activate every SVG affordance, focus lands on what it opened, and other keys do nothing", async () => {
    for (const a of affordances) {
      for (const key of ["Enter", " "]) {
        const label = `${a.name} / ${JSON.stringify(key)}`;
        const { container, unmount } = open(a.url);
        const before = await a.arrange(container);
        const target = a.target(container);
        expect(target.getAttribute("role"), label).toBe("button");
        expect(target.getAttribute("tabindex"), label).toBe("0");
        target.focus();

        // Keys that are not an activation change nothing.
        for (const other of ["a", "ArrowDown", "Tab"]) {
          fireEvent.keyDown(target, { key: other });
          expect(selectionKind(), `${label} after ${other}`).toBe(before);
        }

        fireEvent.keyDown(target, { key });
        await waitFor(() => expect(selectionKind(), label).toBe(a.expectKind));
        expect(document.activeElement, label).toBe(screen.getByTestId("selection-title"));
        unmount();
      }
    }
  });

  it("the outline's + buttons move focus into the frame they open -- the site row and the root alike", async () => {
    open(`/design/${IMPRINT}`);
    const plus = await outlinePlus("site:10.80");
    plus.focus();
    fireEvent.click(plus);
    await screen.findByTestId("form-add-segment");
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
    expect(title()).toBe("Add a segment in 10.80");

    // Tab away, then the root's add: a NEW gesture, so focus moves again.
    const root = screen.getByTestId("outline-add-segment");
    root.focus();
    expect(document.activeElement).toBe(root);
    fireEvent.click(root);
    await waitFor(() => expect(title()).toBe("Add a segment"));
    expect(document.activeElement).toBe(screen.getByTestId("selection-title"));
  });

  it("the action list is an accordion: one form at a time, the open control says so, pressing it again closes it", async () => {
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`);
    const addHost = await screen.findByTestId("action-open-add-host");
    const addConduit = screen.getByTestId("action-open-add-conduit");
    expect(addHost.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryAllByTestId(/^form-/)).toHaveLength(0);

    fireEvent.click(addHost);
    expect(addHost.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId(/^form-/).map((f) => f.getAttribute("data-testid"))).toEqual(["form-add-host"]);

    fireEvent.click(addConduit);
    expect(addHost.getAttribute("aria-expanded")).toBe("false");
    expect(addConduit.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getAllByTestId(/^form-/).map((f) => f.getAttribute("data-testid"))).toEqual(["form-add-conduit"]);

    fireEvent.click(addConduit);
    expect(addConduit.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryAllByTestId(/^form-/)).toHaveLength(0);
  });

  it("staging under a selection the URL makes never pulls the caret to the heading", async () => {
    // The URL's selection is re-derived -- a NEW object -- on every render,
    // unlike an explicit one. Focus is keyed on the explicit selection only;
    // the writer's typing test runs under an explicit (stable) selection, so it
    // cannot tell the two apart. This one can.
    const { puts, api } = server();
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, api);
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    const form = await screen.findByTestId("form-add-host");
    const key = field(form, "host-key");
    key.focus();
    fireEvent.change(key, { target: { value: "address:10.80.20.99" } });
    expect(document.activeElement).toBe(key);
    fireEvent.change(field(form, "gesture-note"), { target: { value: "n" } });
    expect(document.activeElement).toBe(key);

    fireEvent.submit(form);
    await screen.findByTestId("change-row");
    // The store changed and the whole inspector re-rendered; the caret stayed.
    expect(document.activeElement).toBe(key);
    expect(document.activeElement).not.toBe(screen.getByTestId("selection-title"));
    expect(puts).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// claims across save and apply
// ---------------------------------------------------------------------------
describe("S5.1.a: an add stays claimed after its file is SAVED and after an APPLY re-reads it", () => {
  it("keeps the next free /24 past it, and refuses re-adding it, naming the file's add", async () => {
    const { api, puts, applies } = server({ changed: true });
    open(`/design/${IMPRINT}`, api);

    fireEvent.click(await outlinePlus("site:10.80"));
    const form = await screen.findByTestId("form-add-segment");
    expect(field(form, "seg-cidr").value).toBe("10.80.0.0/24");
    fireEvent.change(field(form, "gesture-note"), { target: { value: "the OT lab" } });
    fireEvent.submit(form);
    await screen.findByTestId("change-row");

    // SAVE: the row is written, and the + has moved past it.
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(screen.getByTestId("change-row").getAttribute("data-state")).toBe("saved"));
    expect(puts).toHaveLength(1);

    // APPLY: save + recompile, then the cast and the file are RE-READ.
    fireEvent.click(screen.getByTestId("apply-button"));
    await waitFor(() => expect(applies).toHaveLength(1));
    await screen.findByTestId("apply-result");
    // Wait for the apply to SETTLE: its last step navigates to `?changed=1`
    // (which resets the selection slot), and the cast it reloads must be back.
    // Clicking the + before both have happened races the reset -- run alone,
    // this test lost that race every time before this wait existed.
    await waitFor(() => {
      expect(loc()).toBe(`/design/${IMPRINT}?changed=1`);
      expect(screen.queryByTestId("actions-loading")).toBeNull();
      expect(screen.getByTestId("level0-svg")).toBeTruthy();
    });

    // Re-open the + over the re-read file. (Since the fix round's B2 a changing
    // apply KEEPS the selection -- its `?changed=1` is not a new place.)
    fireEvent.click(await outlinePlus("site:10.80"));
    await waitFor(() =>
      expect(screen.getByTestId("site-add-suggestion").textContent).toContain("10.80.1.0/24"),
    );
    const again = screen.getByTestId("form-add-segment");
    fireEvent.change(field(again, "seg-cidr"), { target: { value: "10.80.0.0/24" } });
    expect(within(again).getByTestId("seg-claim").textContent).toMatch(
      /^10\.80\.0\.0\/24 is already claimed by an? (saved|applied) add/,
    );
    fireEvent.change(field(again, "gesture-note"), { target: { value: "again" } });
    fireEvent.submit(again);
    expect(within(again).getByTestId("gesture-error").textContent).toMatch(/claimed by an? (saved|applied) add/);
    // Nothing new was staged: the one row is the file's own.
    const rows = screen.getAllByTestId("change-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]!.getAttribute("data-state")).not.toBe("pending");
  });
});

// ---------------------------------------------------------------------------
// records
// ---------------------------------------------------------------------------
describe("S5.1.a: the seven gestures, reached ONLY through the new paths, write the S5 record layer's document", () => {
  it("is byte-identical to buildRecord -> reducer -> documentFrom over the same values, and to the literal §3 document", async () => {
    const { api, puts } = server();
    const { container } = open(`/design/${IMPRINT}`, api);
    await screen.findByTestId("level0-svg");
    const stage = async (form: HTMLElement, rows: number): Promise<void> => {
      fireEvent.submit(form);
      await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(rows));
      expect(within(form).queryByTestId("gesture-error")).toBeNull();
    };

    // 1. add: segment -- the MAP site header's +.
    fireEvent.click(mapPlus(container, "site:10.80"));
    let form = await screen.findByTestId("form-add-segment");
    fireEvent.change(field(form, "seg-cidr"), { target: { value: "10.80.31.0/24" } });
    fireEvent.change(field(form, "seg-name"), { target: { value: "seg-10-80-31" } });
    fireEvent.change(within(form).getByTestId("seg-zone"), { target: { value: "simple" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "the OT lab" } });
    await stage(form, 1);

    // 2. add: host -- a tile's ✎, then *Add host here* (listen via the port editor).
    fireEvent.click(tilePencil(container, "10.80.20.0/24"));
    fireEvent.click(await screen.findByTestId("action-open-add-host"));
    form = await screen.findByTestId("form-add-host");
    fireEvent.change(field(form, "host-key"), { target: { value: "address:10.80.20.99" } });
    fireEvent.change(field(form, "host-role"), { target: { value: "domain-controller" } });
    for (const port of ["88", "389"]) {
      fireEvent.change(field(form, "host-listen-port"), { target: { value: port } });
      fireEvent.change(within(form).getByTestId("host-listen-proto"), { target: { value: "tcp" } });
      fireEvent.click(within(form).getByTestId("host-listen-add"));
    }
    fireEvent.change(field(form, "gesture-note"), { target: { value: "the DC answers nothing on the wire" } });
    await stage(form, 2);

    // 3. add: conduit -- *Add conduit to…* on the same selection.
    fireEvent.click(screen.getByTestId("action-open-add-conduit"));
    form = await screen.findByTestId("form-add-conduit");
    fireEvent.change(within(form).getByTestId("conduit-b"), { target: { value: "10.80.72.0/24" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "the OT lab reaches the DC" } });
    await stage(form, 3);

    // 4. suppress -- *Suppress hosts…* on the same selection.
    fireEvent.click(screen.getByTestId("action-open-suppress-hosts"));
    form = await screen.findByTestId("form-suppress-hosts");
    fireEvent.change(within(form).getByTestId("suppress-host-key"), { target: { value: "address:10.80.20.5" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "out of scope: a lab resolver" } });
    await stage(form, 4);

    // 5-8. the host's own actions -- drill to the segment, then its chip.
    fireEvent.click(container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!);
    fireEvent.click(await screen.findByLabelText("domain-controller: hostname:axdc01"));
    await waitFor(() => expect(title()).toBe("Host hostname:axdc01"));

    fireEvent.click(screen.getByTestId("action-open-change-role"));
    form = await screen.findByTestId("form-change-role");
    fireEvent.change(field(form, "role-value"), { target: { value: "file-server" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "it answers SMB" } });
    await stage(form, 5);

    fireEvent.click(screen.getByTestId("action-open-override"));
    form = await screen.findByTestId("form-override-image");
    fireEvent.change(field(form, "image-value"), { target: { value: "debian-12" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "pin the image" } });
    await stage(form, 6);
    form = screen.getByTestId("form-override-resources");
    fireEvent.change(field(form, "res-cpu"), { target: { value: "2" } });
    fireEvent.change(field(form, "res-ram"), { target: { value: "4096" } });
    fireEvent.change(field(form, "res-disk"), { target: { value: "32G" } });
    fireEvent.change(field(form, "gesture-note"), { target: { value: "size it up" } });
    await stage(form, 7);

    fireEvent.click(screen.getByTestId("action-open-suppress"));
    form = await screen.findByTestId("form-suppress");
    fireEvent.change(field(form, "gesture-note"), { target: { value: "out of scope: retired" } });
    await stage(form, 8);

    // One record per gesture -- S5.1.a stages no batch.
    expect(screen.getAllByTestId("change-row")).toHaveLength(8);
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    const written = JSON.stringify(puts[0]);

    // (a) The S5 record layer, fed the SAME values with no UI in between.
    const gestures: Gesture[] = [
      { kind: "add-segment", cidr: "10.80.31.0/24", name: "seg-10-80-31", zoneType: "simple", note: "the OT lab" },
      {
        kind: "add-host",
        key: "address:10.80.20.99",
        segment: "10.80.20.0/24",
        role: "domain-controller",
        listen: [{ port: 88, proto: "tcp" }, { port: 389, proto: "tcp" }],
        note: "the DC answers nothing on the wire",
      },
      { kind: "add-conduit", cidrA: "10.80.20.0/24", cidrB: "10.80.72.0/24", note: "the OT lab reaches the DC" },
      { kind: "suppress-host", key: "address:10.80.20.5", note: "out of scope: a lab resolver" },
      { kind: "change-role", key: "hostname:axdc01", role: "file-server", note: "it answers SMB" },
      { kind: "override-image", key: "hostname:axdc01", image: "debian-12", note: "pin the image" },
      { kind: "override-resources", key: "hostname:axdc01", resources: { cpu: 2, ram: 4096, disk: "32G" }, note: "size it up" },
      { kind: "suppress-host", key: "hostname:axdc01", note: "out of scope: retired" },
    ];
    let state = editReducer(initialEditState(IMPRINT), { type: "loaded", doc: fileDoc() });
    for (const g of gestures) {
      const built = buildRecord(g);
      if (!built.ok) throw new Error(`${g.kind}: ${built.error}`);
      state = editReducer(state, { type: "stage", block: built.staged.block, record: built.staged.record });
    }
    const s5 = documentFrom(state, { schema: "axr-assertions/1", imprint: IMPRINT, as_of: AS_OF, source: SOURCE });
    expect(written).toBe(JSON.stringify(s5));

    // (b) The literal document -- the non-circular anchor: (a) alone would pass
    // for a record layer that is wrong in the same way on both sides (LES-187).
    expect(written).toBe(
      JSON.stringify({
        schema: "axr-assertions/1",
        imprint: IMPRINT,
        as_of: AS_OF,
        source: SOURCE,
        add: [
          { kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31", zone_type: "simple", note: "the OT lab" },
          {
            kind: "host",
            key: "address:10.80.20.99",
            segment: "10.80.20.0/24",
            role: "domain-controller",
            listen: [{ port: 88, proto: "tcp" }, { port: 389, proto: "tcp" }],
            note: "the DC answers nothing on the wire",
          },
          { kind: "conduit", key: "10.80.20.0/24|10.80.72.0/24", note: "the OT lab reaches the DC" },
        ],
        override: [
          { key: "hostname:axdc01", field: "role", value: "file-server", note: "it answers SMB" },
          { key: "hostname:axdc01", field: "image", value: "debian-12", note: "pin the image" },
          { key: "hostname:axdc01", field: "resources", value: { cpu: 2, disk: "32G", ram: 4096 }, note: "size it up" },
        ],
        suppress: [
          { key: "address:10.80.20.5", note: "out of scope: a lab resolver" },
          { key: "hostname:axdc01", note: "out of scope: retired" },
        ],
        etag: "etag-1",
      }),
    );
  });

  it("*Add conduit to…* stages the DEFAULT peer it shows when the operator picks none", async () => {
    // A `<select>` whose state default is not the option it renders first would
    // stage a peer the operator never saw. The first peer is the next segment
    // in CIDR order that is not the selection itself.
    const { api, puts } = server();
    open(`/design/${IMPRINT}/segments/10.80.20.0-24`, api);
    fireEvent.click(await screen.findByTestId("action-open-add-conduit"));
    const form = await screen.findByTestId("form-add-conduit");
    const select = within(form).getByTestId("conduit-b") as HTMLSelectElement;
    expect(select.value).toBe("10.1.10.0/24");
    expect(select.options[0]!.value).toBe("10.1.10.0/24");
    expect([...select.options].map((o) => o.value)).not.toContain("10.80.20.0/24");
    fireEvent.change(field(form, "gesture-note"), { target: { value: "the default" } });
    fireEvent.submit(form);
    await screen.findByTestId("change-row");
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["add"]).toEqual([{ kind: "conduit", key: "10.1.10.0/24|10.80.20.0/24", note: "the default" }]);
  });
});

// ---------------------------------------------------------------------------
// branches no test reached
// ---------------------------------------------------------------------------
describe("S5.1.a: the branches no other test reaches", () => {
  it("a host key the export does not carry keeps its three actions, says so, and stages", async () => {
    // `matched: false` is a legitimate outcome of a suppression, so an unknown
    // key is not an error here -- the component says as much.
    const { api, puts } = server();
    open(`/design/${IMPRINT}/hosts/address:198.51.100.7`, api);
    expect(await screen.findByTestId("selection-unknown-host")).toBeTruthy();
    expect(title()).toBe("Host address:198.51.100.7");
    expect(screen.queryByTestId("host-facts")).toBeNull();
    for (const id of ["change-role", "override", "suppress"]) {
      expect(screen.getByTestId(`action-open-${id}`).hasAttribute("aria-disabled"), id).toBe(false);
    }
    fireEvent.click(screen.getByTestId("action-open-suppress"));
    const form = await screen.findByTestId("form-suppress");
    fireEvent.change(field(form, "gesture-note"), { target: { value: "never seen, never wanted" } });
    fireEvent.submit(form);
    await screen.findByTestId("change-row");
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]!["suppress"]).toEqual([{ key: "address:198.51.100.7", note: "never seen, never wanted" }]);
  });

  it("a segment CIDR that nobody holds is SAID -- no facts are invented for it", async () => {
    // Deliberately silent on which actions a missing segment offers: that is
    // an open question for a segment the file ADDS (see the report), and this
    // CIDR is in neither the export nor the file.
    open(`/design/${IMPRINT}/segments/10.99.99.0-24`);
    const missing = await screen.findByTestId("selection-missing");
    expect(missing.textContent).toContain("10.99.99.0/24");
    expect(title()).toBe("Segment 10.99.99.0/24");
    expect(screen.queryByTestId("segment-facts")).toBeNull();
  });

  /** `ElementActions` over a hand-built graph: the two `unavailable` branches
   *  need a topology no shipped fixture has. */
  function actionsFor(exportData: TopologyExport, selection: Selection): void {
    render(
      <ElementActions
        graph={buildGraph(exportData, trimmedCast)}
        graphState={{ loading: false, error: null, absent: false }}
        selection={selection}
        explicit={null}
        canClear={false}
        edits={initialEditState(IMPRINT)}
        onStage={() => {}}
        onClear={() => {}}
        onSelectSegment={() => {}}
      />,
    );
  }

  function expectUnavailable(id: string, reason: RegExp): void {
    const button = screen.getByTestId(`action-open-${id}`) as HTMLButtonElement;
    // Restated for the fix round's C (LES-299): `aria-disabled`, not
    // `disabled`, so an unavailable action stays in the tab order with its
    // reason.
    expect(button.getAttribute("aria-disabled"), id).toBe("true");
    expect(button.disabled, id).toBe(false);
    const why = screen.getByTestId(`action-why-${id}`);
    expect(why.textContent, id).toMatch(reason);
    expect(button.getAttribute("aria-describedby"), id).toBe(why.id);
    // An unavailable action never opens its form.
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded"), id).toBe("false");
  }

  it("*Add conduit to…* on a twin with ONE segment is unavailable, and says why at the control", () => {
    const only = "10.80.20.0/24";
    actionsFor(
      {
        ...trimmedExport,
        segments: trimmedExport.segments.filter((s) => s.cidr === only),
        nodes: trimmedExport.nodes.filter((n) => n.segment === only || n.segment === null),
      },
      { kind: "segment", cidr: only },
    );
    expectUnavailable("add-conduit", /joins two DIFFERENT segments, and this twin has only this one/);
    // The other actions are unaffected.
    expect(screen.getByTestId("action-open-add-host").hasAttribute("aria-disabled")).toBe(false);
    expect(screen.getByTestId("action-open-suppress-hosts").hasAttribute("aria-disabled")).toBe(false);
  });

  it("*Suppress hosts…* on a segment with no placed host is unavailable, and says why at the control", () => {
    const empty = "10.20.192.0/24";
    actionsFor(
      { ...trimmedExport, nodes: trimmedExport.nodes.filter((n) => n.segment !== empty) },
      { kind: "segment", cidr: empty },
    );
    expectUnavailable("suppress-hosts", /no placed host to suppress/);
    expect(screen.getByTestId("action-open-add-host").hasAttribute("aria-disabled")).toBe(false);
    expect(screen.getByTestId("action-open-add-conduit").hasAttribute("aria-disabled")).toBe(false);
  });

  it("a prefix with no free /24 says FULL and prefills NOTHING -- and a CIDR elsewhere still stages", async () => {
    // A saved `add: segment 10.80.0.0/16` claims every /24 of the prefix. The
    // writer accepts one, so a real file can hold it.
    const file = fileDoc({ add: [{ kind: "segment", key: "10.80.0.0/16", name: "seg-10-80-wide", note: "the whole /16" }] });
    open(`/design/${IMPRINT}`, server({ file }).api);
    fireEvent.click(await outlinePlus("site:10.80"));
    const full = await screen.findByTestId("site-add-full");
    expect(full.textContent).toContain("10.80");
    expect(screen.queryByTestId("site-add-suggestion")).toBeNull();
    const form = screen.getByTestId("form-add-segment");
    // Never a wrong suggestion: an empty field, and an empty name to match.
    expect(field(form, "seg-cidr").value).toBe("");
    expect(field(form, "seg-name").value).toBe("");

    fireEvent.change(field(form, "seg-cidr"), { target: { value: "10.81.0.0/24" } });
    expect(within(form).queryByTestId("seg-claim")).toBeNull();
    fireEvent.change(field(form, "gesture-note"), { target: { value: "elsewhere" } });
    fireEvent.submit(form);
    await waitFor(() => expect(screen.getAllByTestId("change-row")).toHaveLength(2));
    expect(within(form).queryByTestId("gesture-error")).toBeNull();
  });

  it("the MAP's misc + prefills nothing and says the group has no single prefix", async () => {
    const { container } = open(`/design/${IMPRINT}`);
    await screen.findByTestId("level0-svg");
    const plus = mapPlus(container, "site:misc");
    // Restated for the fix round's C (LES-299): the map's `+` carries the
    // OUTLINE's name, and promises no prefill -- the form says the rest.
    expect(plus.getAttribute("aria-label")).toBe("Add a segment in the misc group");
    fireEvent.click(plus);
    const form = await screen.findByTestId("form-add-segment");
    expect(title()).toBe("Add a segment in misc");
    expect(screen.getByTestId("site-add-noprefix").textContent).toMatch(/single segments from different \/16s/);
    expect(field(form, "seg-cidr").value).toBe("");
  });
});
