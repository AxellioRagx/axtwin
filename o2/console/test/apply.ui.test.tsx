/**
 * S6 apply + refresh, wired through the real shell against a fake API with a
 * MOCKED apply endpoint (no real compiler -- that is the gated live-verify).
 * This file proves the WIRING of `docs/design/15-console-canvas.md` §12 O5 and
 * `14` §4:
 *
 *   - apply POSTs, and on the returned (changed) fingerprint the SPA reloads
 *     and HIGHLIGHTS the changed elements for five seconds, then a `?changed=1`
 *     filter keeps the set reachable (fake timers: the glow clears at 5s, the
 *     filter persists);
 *   - a compile failure surfaces the compiler's OWN stderr, verbatim;
 *   - an apply with unsaved pending edits SAVES them first -- the PUT precedes
 *     the POST in call order;
 *   - a no-op apply (unchanged fingerprint) reads as "no change", not a
 *     spurious highlight.
 *
 * The pure diff->change-set reduction is `changes.test.ts`.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
import { TestApp } from "../src/routes";
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

function existingDoc(overrides: Partial<AssertionsDoc> = {}): AssertionsDoc {
  return {
    imprint_id: IMPRINT,
    path: `/b/${IMPRINT}/${IMPRINT}.assertions.yaml`,
    exists: true,
    etag: "etag-1",
    schema: "axr-assertions/1",
    imprint: IMPRINT,
    as_of: "2026-09-10T00:00:00Z",
    source: "operator: tester",
    add: [],
    override: [],
    suppress: [],
    warnings: [],
    ...overrides,
  };
}

function applyResponse(part: Partial<ApplyResponse>): ApplyResponse {
  return {
    imprint_id: IMPRINT,
    fingerprint_before: "fp0",
    fingerprint_after: "fp1",
    changed: true,
    pit_code_fingerprint_before: null,
    pit_code_fingerprint_after: null,
    // The ordinary compiler outcome: both build stamps read, same build, so
    // `CompilerChange` renders nothing (GAP-087).
    cast_code_fingerprint_before: "build-a",
    cast_code_fingerprint_after: "build-a",
    compiler_changed: false,
    compiler_note: null,
    assertions: existingDoc({ etag: "etag-2" }),
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
    ...part,
  };
}

const EMPTY_SECTION = { added: [], removed: [], changed: [] };

function diffResponse(part: Partial<ImprintDiff>): ImprintDiff {
  return {
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
    ...part,
  };
}

interface FakeOpts {
  assertions?: AssertionsDoc;
  apply?: ConsoleApi["apply"];
  diff?: ImprintDiff;
  /** MUST-FIX 1(b): a diff fetch that fails outright (a pruned/unknown
   *  fingerprint, a network blip) -- distinct from `diff`, which shapes a
   *  SUCCESSFUL response's content. */
  getDiff?: ConsoleApi["getDiff"];
  calls?: string[];
  applies?: Array<{ id: string; etag: string | null; document: unknown }>;
}

function fakeApi(opts: FakeOpts = {}): ConsoleApi {
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  const doc = opts.assertions ?? existingDoc();
  const calls = opts.calls;
  const defaultApply: ConsoleApi["apply"] = (id, req) => {
    calls?.push("apply");
    opts.applies?.push({ id, etag: req.etag ?? null, document: req.document });
    return Promise.resolve(applyResponse({ imprint_id: id }));
  };
  return {
    listImprints: () =>
      Promise.resolve({
        imprints: [{ id: IMPRINT, hosts: 40, segments: 6, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent" }],
        skipped: [],
      } satisfies ImprintList),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROVENANCE),
    getTopology: () => Promise.resolve(trimmedExport as TopologyExport),
    getCast: (id) => Promise.resolve({ ...CAST, id, twin_config_id: id }),
    getAssertions: () => Promise.resolve(doc),
    putAssertions: (id, document, _etag) => {
      calls?.push("put");
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
    },
    // `plane: null` -- a bundle with no saved layout carries no plane stamp
    // either, which is the LEGACY state the reader must handle (R2).
    getLayout: () => Promise.resolve({ imprint_id: IMPRINT, path: "", exists: false, etag: null, schema: "axr-layout/1", imprint: IMPRINT, pins: {}, plane: null }),
    putLayout: unused("putLayout"),
    apply: opts.apply ?? defaultApply,
    getDiff: opts.getDiff ?? (() => Promise.resolve(opts.diff ?? diffResponse({}))),
  };
}

/**
 * Flush chained promises (the fetch mocks resolve as microtasks) and any 0ms
 * timers, WITHOUT advancing far enough to trip the 5s highlight timer. Called
 * a few times because the apply flow chains several awaits (POST -> reload ->
 * diff -> re-render).
 */
async function settle(times = 4): Promise<void> {
  for (let i = 0; i < times; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
  }
}

function tileRect(container: HTMLElement, cidr: string): Element | null {
  return container.querySelector(`[data-pinnable="segment:${cidr}"] rect.tile`);
}

afterEach(() => {
  vi.useRealTimers();
});

describe("apply -> reload -> highlight for 5s -> ?changed=1 keeps the set (15 §12 O5)", () => {
  it("glows the changed elements, clears the glow at 5s, and leaves them reachable", async () => {
    vi.useFakeTimers();
    // The diff names a changed OBSERVED segment (a tile) and an authored
    // suppressed key that IS an infra device's id on level 0.
    const diff = diffResponse({
      observed: {
        segments: { added: ["10.80.20.0/24"], removed: [], changed: [] },
        nodes: EMPTY_SECTION,
      },
      suppressed_keys: { added: ["address:10.80.20.5"], removed: [], changed: [] },
    });
    const { container } = render(<TestApp client={fakeApi({ diff })} url={`/design/${IMPRINT}`} />);
    await settle();

    // Nothing highlighted before an apply.
    expect(tileRect(container, "10.80.20.0/24")!.classList.contains("is-changed-glow")).toBe(false);
    expect(tileRect(container, "10.80.20.0/24")!.classList.contains("is-changed")).toBe(false);

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    // The changed result is reported, and the URL now carries ?changed=1 (the
    // "changes" chip only renders when it is on).
    expect(screen.getByTestId("apply-result")).toBeTruthy();
    expect(screen.getByTestId("filter-changed")).toBeTruthy();

    // The changed tile AND the changed device carry BOTH the transient glow and
    // the persistent `is-changed` mark within the five-second window.
    const tile = tileRect(container, "10.80.20.0/24")!;
    expect(tile.classList.contains("is-changed-glow")).toBe(true);
    expect(tile.classList.contains("is-changed")).toBe(true);
    const device = container.querySelector('[aria-label="dns-resolver: address:10.80.20.5"] rect.device')!;
    expect(device.classList.contains("is-changed-glow")).toBe(true);

    // After five seconds the transient glow clears...
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    const tileAfter = tileRect(container, "10.80.20.0/24")!;
    expect(tileAfter.classList.contains("is-changed-glow")).toBe(false);
    // ...but the set is STILL reachable: `?changed=1` keeps `is-changed` on and
    // the "changes" filter chip present.
    expect(tileAfter.classList.contains("is-changed")).toBe(true);
    expect(screen.getByTestId("filter-changed")).toBeTruthy();
  });
});

describe("a compile failure surfaces the compiler's OWN stderr, verbatim (14 §4)", () => {
  it("shows the exact stderr, not a generic banner", async () => {
    vi.useFakeTimers();
    const STDERR = "cast: error at node axdc01: unknown role 'file-serverr'\n  (did you mean 'file-server'?)\nexit 2";
    const rejectApply: ConsoleApi["apply"] = () =>
      Promise.reject(
        new ConsoleApiError({
          kind: "upstream-failed",
          status: 502,
          message: "the recompile failed",
          detail: { stderr: STDERR },
          url: "",
        }),
      );
    render(<TestApp client={fakeApi({ apply: rejectApply })} url={`/design/${IMPRINT}`} />);
    await settle();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    // The compiler's own words, verbatim -- and NO changed result / no highlight.
    expect(screen.getByTestId("apply-stderr").textContent).toBe(STDERR);
    expect(screen.queryByTestId("apply-result")).toBeNull();
    expect(screen.queryByTestId("filter-changed")).toBeNull();
  });
});

describe("apply IS save + recompile: the save PUT precedes the apply POST (14 §4)", () => {
  it("saves pending edits first, in call order", async () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const applies: FakeOpts["applies"] = [];
    // A no-op recompile keeps this test focused on ORDER (no diff needed).
    const apply: ConsoleApi["apply"] = (id, req) => {
      calls.push("apply");
      applies!.push({ id, etag: req.etag ?? null, document: req.document });
      return Promise.resolve(applyResponse({ imprint_id: id, changed: false, fingerprint_after: "fp0" }));
    };
    render(<TestApp client={fakeApi({ calls, applies, apply })} url={`/design/${IMPRINT}/hosts/hostname:axdc01`} />);
    await settle();

    // Stage a PENDING suppress so apply has something to save first.
    // S5.1.a: the gesture is now an ACTION on the selected host (the zoom
    // selects it) rather than a free-standing form, so the action is opened
    // first. Only the path changed -- the record and the call order asserted
    // below are the S5/S6 ones, unaltered (LES-299).
    fireEvent.click(screen.getByTestId("action-open-suppress"));
    const suppress = screen.getByTestId("form-suppress");
    fireEvent.change(within(suppress).getByTestId("gesture-note"), { target: { value: "out of scope" } });
    fireEvent.submit(suppress);

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    // The PUT happened, then the POST -- in that order.
    expect(calls).toContain("put");
    expect(calls).toContain("apply");
    expect(calls.indexOf("put")).toBeLessThan(calls.indexOf("apply"));
    // The apply carried the FRESH etag the PUT returned (never the pre-save one).
    expect(applies![0]!.etag).toBe("etag-2");
  });
});

describe("a no-op apply reads as 'no change', not a spurious highlight (14 §3 determinism)", () => {
  it("shows the no-change notice and does not highlight or set ?changed=1", async () => {
    vi.useFakeTimers();
    const noChange: ConsoleApi["apply"] = (id) =>
      Promise.resolve(applyResponse({ imprint_id: id, changed: false, fingerprint_before: "fp0", fingerprint_after: "fp0" }));
    const { container } = render(<TestApp client={fakeApi({ apply: noChange })} url={`/design/${IMPRINT}`} />);
    await settle();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    expect(screen.getByTestId("apply-nochange")).toBeTruthy();
    expect(screen.queryByTestId("apply-result")).toBeNull();
    // No ?changed=1 (no chip), and nothing on the map glows.
    expect(screen.queryByTestId("filter-changed")).toBeNull();
    expect(tileRect(container, "10.80.20.0/24")!.classList.contains("is-changed-glow")).toBe(false);
    expect(tileRect(container, "10.80.20.0/24")!.classList.contains("is-changed")).toBe(false);
  });
});

describe("MUST-FIX 1: a changed apply whose diff could not be computed must say so, NEVER 'nothing moved' (LES-240)", () => {
  it("history_written: false -- the honest 'diff unavailable' message, carrying the server's warning, never the false no-highlight claim", async () => {
    vi.useFakeTimers();
    const WARNING =
      "the bundle carried none of its canonical files before this apply, so no content fingerprint and no history snapshot could be written -- `GET .../diff?since=` has nothing to compare";
    const apply: ConsoleApi["apply"] = (id) =>
      Promise.resolve(
        applyResponse({
          imprint_id: id,
          changed: true,
          history_written: false,
          warnings: [WARNING],
        }),
      );
    render(<TestApp client={fakeApi({ apply })} url={`/design/${IMPRINT}`} />);
    await settle();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    expect(screen.getByTestId("apply-result")).toBeTruthy();
    // The honest "couldn't be computed" message -- carrying the server's own
    // warning -- and NEVER the "nothing moved" one.
    const note = screen.getByTestId("apply-diff-unavailable");
    expect(note.textContent).toContain("couldn't be computed");
    expect(note.textContent).toContain(WARNING);
    expect(note.textContent).not.toContain("no observed element on this map moved");
    expect(screen.queryByTestId("apply-changed-note")).toBeNull();
  });

  it("a failed diff fetch (e.g. a pruned/unknown fingerprint) -- the same honest message, distinct from a genuinely empty diff", async () => {
    vi.useFakeTimers();
    const failingDiff: ConsoleApi["getDiff"] = () =>
      Promise.reject(
        new ConsoleApiError({
          kind: "not-found",
          status: 404,
          message: "unknown fingerprint",
          url: "",
        }),
      );
    // The default `apply` response has `history_written: true` + a
    // `fingerprint_before`, so `onApply` DOES attempt the diff fetch here --
    // it is that fetch, not the write bookkeeping, that fails this time.
    render(<TestApp client={fakeApi({ getDiff: failingDiff })} url={`/design/${IMPRINT}`} />);
    await settle();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    expect(screen.getByTestId("apply-result")).toBeTruthy();
    const note = screen.getByTestId("apply-diff-unavailable");
    expect(note.textContent).toContain("couldn't be computed");
    expect(note.textContent).not.toContain("no observed element on this map moved");
    expect(screen.queryByTestId("apply-changed-note")).toBeNull();
  });
});

describe("SHOULD-FIX 2: a stale apply must not force-navigate the operator away from where they moved on to", () => {
  it("apply on the network level, then drill into a segment before the POST resolves -- no snap-back", async () => {
    vi.useFakeTimers();
    let resolveApply: ((r: ApplyResponse) => void) | null = null;
    const pendingApply: ConsoleApi["apply"] = () =>
      new Promise<ApplyResponse>((resolve) => {
        resolveApply = resolve;
      });
    render(<TestApp client={fakeApi({ apply: pendingApply })} url={`/design/${IMPRINT}`} />);
    await settle();
    const map = screen.getByTestId("level0-svg");

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    // The apply is genuinely in flight -- the button is disabled, and there is
    // no result yet (the promise above has not been resolved).
    expect((screen.getByTestId("apply-button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("apply-result")).toBeNull();

    // The operator drills into a segment BEFORE the pending apply resolves.
    fireEvent.click(within(map).getByLabelText(/Segment 10\.80\.20\.0\/24/));
    await settle();
    expect(screen.getByTestId("level1-svg")).toBeTruthy();
    expect(screen.queryByTestId("level0-svg")).toBeNull();

    // NOW the apply resolves. Its stale continuation must NOT yank the
    // browser back to the network-level route it started from.
    resolveApply!(applyResponse({ imprint_id: IMPRINT, changed: true }));
    await settle();

    expect(screen.queryByTestId("level0-svg")).toBeNull();
    expect(screen.getByTestId("level1-svg")).toBeTruthy();
  });

  it("apply on an imprint, then return to the catalog before the POST resolves -- no snap-back (an imprint switch, LES-259's remount key)", async () => {
    vi.useFakeTimers();
    let resolveApply: ((r: ApplyResponse) => void) | null = null;
    const pendingApply: ConsoleApi["apply"] = () =>
      new Promise<ApplyResponse>((resolve) => {
        resolveApply = resolve;
      });
    render(<TestApp client={fakeApi({ apply: pendingApply })} url={`/design/${IMPRINT}`} />);
    await settle();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    expect((screen.getByTestId("apply-button") as HTMLButtonElement).disabled).toBe(true);

    // The operator returns to the catalog root -- a DIFFERENT imprint context
    // -- which UNMOUNTS this workspace (`key={imprintId}`, LES-259) before
    // the apply resolves.
    fireEvent.click(screen.getByText("imprints"));
    await settle();
    expect(screen.queryByTestId("level0-svg")).toBeNull();
    expect(screen.queryByTestId("edit-panel")).toBeNull();

    resolveApply!(applyResponse({ imprint_id: IMPRINT, changed: true }));
    await settle();

    // Still at the catalog -- the stale apply's continuation did not navigate
    // the (never-unmounted) shell back to the imprint it started from.
    expect(screen.queryByTestId("level0-svg")).toBeNull();
    expect(screen.getByText("Pick an imprint from the outline to open its canvas.")).toBeTruthy();
  });
});

describe("a COMPILER change is its own apply outcome (GAP-087)", () => {
  /** The measured case: aximprint HEAD had renamed 11 node groups since the
   *  bundle was cast, so one suppression produced 22 hosts added / 23 removed
   *  while the Console honestly reported "1 element changed". */
  const churnDiff = diffResponse({
    observed: {
      segments: { added: ["10.80.20.0/24"], removed: [], changed: [] },
      nodes: EMPTY_SECTION,
    },
    twin: {
      hosts: {
        added: Array.from({ length: 22 }, (_, i) => `te-new-${i}`),
        removed: Array.from({ length: 23 }, (_, i) => `te-old-${i}`),
        changed: [],
      },
      segments: EMPTY_SECTION,
      conduits: EMPTY_SECTION,
    },
  });

  const compilerMoved: ConsoleApi["apply"] = (id) =>
    Promise.resolve(
      applyResponse({
        imprint_id: id,
        changed: true,
        cast_code_fingerprint_before: "0e2878a932b8b0f8",
        cast_code_fingerprint_after: "abcdef0123456789",
        compiler_changed: true,
        compiler_note: "the compiler changed between the cast and this apply",
      }),
    );

  it("says the compiler moved, and sizes it by the TWIN churn the map cannot show", async () => {
    vi.useFakeTimers();
    render(
      <TestApp
        client={fakeApi({ apply: compilerMoved, diff: churnDiff })}
        url={`/design/${IMPRINT}`}
      />,
    );
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    const note = screen.getByTestId("apply-compiler-changed");
    expect(note.textContent).toContain("The compiler changed");
    // The build hashes are shown in FULL -- this is the value an operator
    // compares against the bundle's own `cast_invocation`.
    expect(note.textContent).toContain("0e2878a932b8b0f8 \u2192 abcdef0123456789");
    expect(note.textContent).toContain("22 hosts added and 23 removed in the twin");
    expect(note.textContent).toContain("unrelated to your edit");
    // NOT folded into the ordinary changed sentence, which still says what it
    // honestly measures on THIS map -- both are rendered, distinctly.
    expect(screen.getByTestId("apply-changed-note").textContent).toContain(
      "1 element changed on this map",
    );
  });

  it("renders on a NO-CHANGE apply too -- it is orthogonal to `changed`", async () => {
    vi.useFakeTimers();
    // A compiler upgrade whose output happens to be byte-identical. Rare, and
    // exactly the case a render nested inside the `changed` branch would miss.
    const quiet: ConsoleApi["apply"] = (id) =>
      Promise.resolve(
        applyResponse({
          imprint_id: id, changed: false,
          fingerprint_before: "fp0", fingerprint_after: "fp0",
          cast_code_fingerprint_before: "build-a",
          cast_code_fingerprint_after: "build-b",
          compiler_changed: true,
          compiler_note: "the compiler changed between the cast and this apply",
        }),
      );
    render(<TestApp client={fakeApi({ apply: quiet })} url={`/design/${IMPRINT}`} />);
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    expect(screen.getByTestId("apply-nochange")).toBeTruthy();
    const note = screen.getByTestId("apply-compiler-changed");
    expect(note.textContent).toContain("The compiler changed");
    // No diff is fetched on the no-op path, so the churn is UNKNOWN -- said
    // as unknown, never as "0 added and 0 removed".
    expect(note.textContent).toContain("is unknown here");
    expect(note.textContent).not.toContain("0 hosts added");
  });

  it("an UNREADABLE build stamp is its own outcome, never the quiet one (LES-273)", async () => {
    vi.useFakeTimers();
    const cannotTell: ConsoleApi["apply"] = (id) =>
      Promise.resolve(
        applyResponse({
          imprint_id: id, changed: true,
          cast_code_fingerprint_before: "build-a",
          cast_code_fingerprint_after: null,
          compiler_changed: null,
          compiler_note:
            "whether the compiler changed ... could not be determined: the "
            + "recompiled bundle's export carries no `cast_invocation` block",
        }),
      );
    render(<TestApp client={fakeApi({ apply: cannotTell })} url={`/design/${IMPRINT}`} />);
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    const note = screen.getByTestId("apply-compiler-unknown");
    expect(note.textContent).toContain("could not be determined");
    // The server's OWN reason travels with it (LES-273), and the CHANGED
    // render -- which would assert a move nobody measured -- is absent.
    expect(note.textContent).toContain("no `cast_invocation` block");
    expect(screen.queryByTestId("apply-compiler-changed")).toBeNull();
    // ONE sentence: the server's note is rendered as-is, not stacked under a
    // near-identical Console lead (fix round 1, finding 3).
    expect(note.textContent!.match(/could not be determined/g)).toHaveLength(1);
    expect(note.textContent!.startsWith("Whether the compiler changed ...")).toBe(true);
  });

  it("the note is said ONCE even when the server also lists it in `warnings`", async () => {
    vi.useFakeTimers();
    // A server that (like this round's first cut) ALSO appended the note to
    // `warnings` must not get it printed twice in the same block -- the
    // warnings are printed in the diff-unavailable branch, so fail the diff.
    const NOTE =
      "whether the compiler changed between the cast and this apply could not be determined: X";
    const both: ConsoleApi["apply"] = (id) =>
      Promise.resolve(
        applyResponse({
          imprint_id: id, changed: true,
          cast_code_fingerprint_before: "a", cast_code_fingerprint_after: null,
          compiler_changed: null, compiler_note: NOTE,
          warnings: [NOTE, "an unrelated warning"],
        }),
      );
    const failingDiff: ConsoleApi["getDiff"] = () =>
      Promise.reject(new ConsoleApiError({ kind: "not-found", status: 404, message: "gone", url: "" }));
    render(
      <TestApp client={fakeApi({ apply: both, getDiff: failingDiff })} url={`/design/${IMPRINT}`} />,
    );
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    const block = screen.getByTestId("apply-result");
    expect(block.textContent!.match(/could not be determined/gi)).toHaveLength(1);
    expect(screen.getByTestId("apply-diff-unavailable").textContent).toContain("an unrelated warning");
  });

  it("an undetermined verdict on a NO-OP apply does not raise a population difference that is not there", async () => {
    vi.useFakeTimers();
    // Nothing moved (`changed: false`), so "a population difference below may
    // not be yours" is a moot question -- the server drops it from its note,
    // and the Console's fallback sentence (no note sent) drops it too.
    const moot: ConsoleApi["apply"] = (id) =>
      Promise.resolve(
        applyResponse({
          imprint_id: id, changed: false,
          fingerprint_before: "fp0", fingerprint_after: "fp0",
          cast_code_fingerprint_before: null, cast_code_fingerprint_after: null,
          compiler_changed: null, compiler_note: null,
        }),
      );
    render(<TestApp client={fakeApi({ apply: moot })} url={`/design/${IMPRINT}`} />);
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    const note = screen.getByTestId("apply-compiler-unknown");
    expect(note.textContent).toContain("could not be determined");
    expect(note.textContent).not.toMatch(/population difference/);
    expect(screen.getByTestId("apply-nochange")).toBeTruthy();
  });

  it("an OLDER server that omits the verdict renders nothing -- never a change nobody measured", async () => {
    vi.useFakeTimers();
    const old: ConsoleApi["apply"] = (id) => {
      const res = applyResponse({ imprint_id: id, changed: true }) as unknown as Record<string, unknown>;
      // Predates GAP-087: none of the four fields exist on the wire.
      for (const k of [
        "compiler_changed", "compiler_note",
        "cast_code_fingerprint_before", "cast_code_fingerprint_after",
      ]) {
        delete res[k];
      }
      return Promise.resolve(res as unknown as ApplyResponse);
    };
    render(<TestApp client={fakeApi({ apply: old })} url={`/design/${IMPRINT}`} />);
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    expect(screen.getByTestId("apply-result")).toBeTruthy();
    expect(screen.queryByTestId("apply-compiler-changed")).toBeNull();
    expect(screen.queryByTestId("apply-compiler-unknown")).toBeNull();
    expect(screen.getByTestId("apply-result").textContent).not.toContain("\u2014 \u2192 \u2014");
  });

  it("a SECOND apply does not size itself by the FIRST one's churn", async () => {
    vi.useFakeTimers();
    // The no-op path fetches no diff at all, so a churn left over from the
    // previous apply would be presented as this apply's measurement -- a
    // number nobody took, attached to a different event.
    let call = 0;
    const thenNoOp: ConsoleApi["apply"] = (id) => {
      call += 1;
      return Promise.resolve(
        call === 1
          ? applyResponse({ imprint_id: id, changed: true })
          : applyResponse({
              imprint_id: id, changed: false,
              fingerprint_before: "fp0", fingerprint_after: "fp0",
              cast_code_fingerprint_before: "build-a",
              cast_code_fingerprint_after: "build-b",
              compiler_changed: true,
              compiler_note: "the compiler changed between the cast and this apply",
            }),
      );
    };
    render(
      <TestApp
        client={fakeApi({ apply: thenNoOp, diff: churnDiff })}
        url={`/design/${IMPRINT}`}
      />,
    );
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    // The first apply DID read a churn (it is not rendered -- that apply's
    // compiler did not move -- but it is now in state).
    expect(screen.queryByTestId("apply-compiler-changed")).toBeNull();

    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();
    const note = screen.getByTestId("apply-compiler-changed");
    expect(note.textContent).toContain("is unknown here");
    expect(note.textContent).not.toContain("22 hosts added");
  });

  it("says NOTHING when the compiler did not change -- the ordinary outcome stays quiet", async () => {
    vi.useFakeTimers();
    // The shared `applyResponse` fixture is `compiler_changed: false`.
    render(<TestApp client={fakeApi()} url={`/design/${IMPRINT}`} />);
    await settle();
    fireEvent.click(screen.getByTestId("apply-button"));
    await settle();

    expect(screen.queryByTestId("apply-compiler-changed")).toBeNull();
    expect(screen.queryByTestId("apply-compiler-unknown")).toBeNull();
    expect(screen.getByTestId("apply-result")).toBeTruthy();
  });
});
