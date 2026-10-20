/**
 * The Design area on a HAND-AUTHORED twin (Unified Console design/16 rule 1),
 * through the real router and shell, with the real corp-twin `/cast`
 * (`fixtures/corp-twin-cast.json`): the declared design is DRAWN, one plain
 * line says what it is (technical reasons behind "details"), no jargon is shown,
 * the Actions panel says what IS possible, the outline labels the two kinds,
 * and the bridge to the live twin works both ways.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConsoleApiError, type ConsoleApi } from "../src/api/client";
import type { RuntimeApi } from "../src/api/runtime";
import type { RangeDoc } from "../src/api/runtimeTypes";
import type { AssertionsDoc, CastSummary, ImprintList, ImprintProvenance } from "../src/api/types";
import { TestApp } from "../src/routes";
import castJson from "./fixtures/corp-twin-cast.json";
import { corpTwinHosts, fakeRuntime, pending } from "./helpers/runtime";

const CAST = castJson as unknown as CastSummary;
const ID = "corp-twin";
const DEPLOY = "corp-twin-07cfcdb2";

const CATALOG: ImprintList = {
  imprints: [
    { id: "corp-twin", hosts: 203, segments: 54, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent", has_topology_export: false },
    { id: "corpapcn-twin", hosts: 137, segments: 27, as_of: "", mean_confidence: 0, has_provenance: false, confidence_source: "absent", has_topology_export: true },
    { id: "corpapcn-golden", hosts: 156, segments: 33, as_of: "x", mean_confidence: 0.7, has_provenance: true, confidence_source: "sidecar", has_topology_export: true },
  ],
  skipped: [],
};

const PROVENANCE_ABSENT: ImprintProvenance = {
  imprint_id: ID,
  rows: [],
  sidecar_schema: null,
  sidecar_shape: "absent",
  evidence_source: "none",
  code_fingerprint: null,
  population: {},
  warnings: [],
} as unknown as ImprintProvenance;

const ASSERTIONS: AssertionsDoc = {
  imprint_id: ID,
  path: "twins/corp-twin/corp-twin.assertions.yaml",
  exists: false,
  etag: null,
  schema: "axr-assertions/1",
  imprint: ID,
  as_of: null,
  source: null,
  add: [],
  override: [],
  suppress: [],
  warnings: [],
} as unknown as AssertionsDoc;

function designApi(): ConsoleApi {
  const notFound = new ConsoleApiError({ kind: "not-found", status: 404, message: "no topology-export for 'corp-twin'", url: "" });
  const unused = (name: string) => () => Promise.reject(new Error(`${name} not used`));
  return {
    listImprints: () => Promise.resolve(CATALOG),
    getImprint: unused("getImprint"),
    getProvenance: () => Promise.resolve(PROVENANCE_ABSENT),
    getTopology: () => Promise.reject(notFound),
    getCast: () => Promise.resolve(CAST),
    getAssertions: () => Promise.resolve(ASSERTIONS),
    putAssertions: unused("putAssertions"),
    getLayout: () => Promise.resolve({ path: "twins/corp-twin/generated/layout.json", exists: false, etag: null, schema: "axr-layout/1", pins: {} } as never),
    putLayout: unused("putLayout"),
    apply: unused("apply"),
    getDiff: unused("getDiff"),
  };
}

function range(): RangeDoc {
  const now = Date.now() / 1000;
  return {
    generated_at: now,
    targets: [
      {
        name: "dev45",
        default: true,
        node: "dev45",
        api_url: "https://x",
        reachability: { state: "reachable", checked_at: now, reason: null },
        store: { ok: true, reason: null },
        twins: [
          {
            deploy_id: DEPLOY, twin_id: ID, status: "completed", plan_id: "p", started_at: now, updated_at: now,
            completed_at: now, cluster: "dev45", host_count: 106, persona_instance_count: 84,
            siblings: { tap: [], tfc: [], exr: [] }, warnings: [],
          },
        ],
      },
    ],
  };
}

function open(url: string, runtime: Partial<RuntimeApi> = {}): void {
  render(
    <TestApp
      client={designApi()}
      runtime={fakeRuntime({ getTargets: () => pending(), getRange: () => Promise.resolve(range()), ...runtime })}
      url={url}
    />,
  );
}

describe("a hand-authored twin in the Design area", () => {
  it("DRAWS its declared design, with one plain line saying what it is", async () => {
    open(`/design/${ID}`);
    expect(await screen.findByTestId("level0-svg")).toBeTruthy();
    const status = screen.getByTestId("declared-status");
    expect(status.querySelector("p")!.textContent).toBe(
      "Hand-authored twin — showing its declared design from twins/corp-twin/te.yaml. There is no capture evidence (no aximprint export), so nothing here is observed.",
    );
    // The technical reasons are behind a closed disclosure.
    const details = status.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toMatch(/answers 404/);
    expect(screen.queryByTestId("graph-absent")).toBeNull();
  });

  it("shows no jargon: no sidecar_shape, no 'no export' / 'predates it', no evidence-absent banner", async () => {
    open(`/design/${ID}?summary=1`);
    await screen.findByTestId("cast-numbers");
    // The whole page, everything not tucked behind a "details" disclosure.
    const visible = [...document.body.querySelectorAll("p, div.note, span, dd")]
      .filter((el) => !el.closest("details"))
      .map((el) => el.textContent ?? "")
      .join(" ");
    expect(visible).not.toMatch(/sidecar_shape|predates it|no export|no topology export/);
    expect(screen.queryByTestId("evidence-absent")).toBeNull();
    expect(screen.getByTestId("cast-numbers").textContent).toMatch(/no capture for this twin/);
  });

  it("the Actions panel says what IS possible; no assertion gesture is offered", async () => {
    open(`/design/${ID}`);
    const actions = await screen.findByTestId("actions-hand-authored");
    expect(actions.textContent).toMatch(/edit twins\/corp-twin\/te\.yaml and redeploy with axrange te deploy --config corp-twin --update/);
    expect(screen.queryAllByTestId(/^action-open-/)).toHaveLength(0);
    expect(screen.queryByTestId("actions-absent")).toBeNull();
  });

  it("a segment drills to its declared hosts, labelled declared -- never observed", async () => {
    open(`/design/${ID}/segments/10.180.64.0-20`);
    const svg = await screen.findByTestId("level1-svg");
    expect(svg.querySelectorAll("[data-testid='host-chip'], .chip").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/\bobserved\b.*declared host/);
  });

  it("the legend shows ONE class, declared -- not the six evidence classes", async () => {
    open(`/design/${ID}`);
    expect((await screen.findByTestId("legend-declared")).textContent).toMatch(/declared.*nothing here is observed/);
    await waitFor(() => expect(within(screen.getByTestId("legend")).queryByText("observed")).toBeNull());
  });

  it("a declared host states no measurement: no occupancy, no ghost row", async () => {
    const id = CAST.node_roster!.find((n) => n.kind !== "router")!.node_ids[0]!;
    open(`/design/${ID}/hosts/hostname:${id}`);
    expect((await screen.findByTestId("host-declared")).textContent).toMatch(/Declared in the twin config/);
    const table = document.querySelector("table.facts2")!;
    expect(table.textContent).not.toMatch(/occupancy|100%/);
    expect(table.textContent).toMatch(/allocated at deploy time/);
    expect(table.textContent).toMatch(/declared/);
  });

  it("the matrix says plainly it needs capture evidence", async () => {
    open(`/design/${ID}/matrix`);
    expect((await screen.findByTestId("matrix-declared")).textContent).toMatch(/needs capture evidence — this twin has none/);
  });

  it("the outline labels the two kinds", async () => {
    open(`/design/${ID}`);
    const outline = await screen.findByLabelText("Imprints");
    await waitFor(() => expect(outline.textContent).toMatch(/corp-twin.*hand-authored/));
    expect(outline.textContent).toMatch(/corpapcn-twin.*from capture/);
    expect(outline.textContent).not.toMatch(/no sidecar/);
  });
});

describe("the bridge between a design and its live twin", () => {
  it("Design -> live: 'Running on dev45 as corp-twin-07cfcdb2 — open the live twin'", async () => {
    open(`/design/${ID}`);
    const bridge = await screen.findByTestId("live-bridge");
    expect(bridge.textContent).toBe(`Running on dev45 as ${DEPLOY} — open the live twin`);
    expect(within(bridge).getByTestId("open-live-twin").getAttribute("href")).toBe(`/twin/${DEPLOY}`);
  });

  it("no bridge when the twin is not deployed, or the range cannot be read", async () => {
    open(`/design/${ID}`, { getRange: () => Promise.resolve({ ...range(), targets: [{ ...range().targets[0]!, twins: [] }] }) });
    await screen.findByTestId("level0-svg");
    expect(screen.queryByTestId("live-bridge")).toBeNull();
  });

  it("live -> Design: the Twin page links back to /design/<twin id>", async () => {
    open(`/twin/${DEPLOY}`, { getHosts: () => Promise.resolve(corpTwinHosts()) });
    const link = await screen.findByTestId("open-design");
    expect(link.getAttribute("href")).toBe(`/design/${ID}`);
    fireEvent.click(link);
    expect(await screen.findByTestId("declared-status")).toBeTruthy();
  });
});
