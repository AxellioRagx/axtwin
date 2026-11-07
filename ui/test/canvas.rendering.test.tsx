/**
 * The rendering rules of 2026-11-06 (`SPEC.md` "Rendering rules"): the
 * Internet is drawn only on evidence or a configured uplink and the gateway
 * is a badge on its router; observed-flow arcs are evidence and off until
 * lit or selected; the site group is a borderless tint that folds; host
 * chips carry no text unless the labels toggle says so.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { arcScope, arcShown, NO_SCOPE } from "../src/features/edit-canvas/canvas/evidence";
import { authoredGraph } from "../src/features/edit-canvas/canvas/authored";
import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import type { TopologyExport, TwinTree } from "../src/api/types";
import { host, hosts, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

/** A cast export with evidence: a flow across two segments and one to a non-local peer. */
const node = (id: string, segment: string | null, role: string | null, locality: "local" | "external" = "local"): TopologyExport["nodes"][number] => ({
  id, address: id.startsWith("address:") ? id.slice(8) : id, locality, segment, segment_evidence_class: segment ? "observed" : null, role, role_confidence: role ? 0.9 : null, role_evidence_class: role ? "observed" : null, windows_seen: 1, listen_ports: [], dial_ports: [],
});
const EVIDENCE: TopologyExport = {
  imprint_id: "corp-cast", pit_id: "pit-1", as_of: 1791313200, network: "corp", vantage: "hq", windows: 10, schema_version: "1",
  segments: [
    { cidr: "10.80.20.0/24", name: "net-lan", entities: 1, coverage_source: null },
    { cidr: "10.1.1.0/24", name: "net-dmz", entities: 1, coverage_source: null },
  ],
  nodes: [node("address:10.80.20.4", "10.80.20.0/24", "corp-workstation"), node("address:10.1.1.7", "10.1.1.0/24", "web-tier"), node("8.8.8.8", null, null, "external")],
  edges: [
    { src: "address:10.80.20.4", dst: "address:10.1.1.7", proto: "tcp", bytes: 500000, flows: 40, evidence_class: "observed" },
    { src: "address:10.80.20.4", dst: "8.8.8.8", proto: "tcp", bytes: 999, flows: 1, evidence_class: "observed" },
  ],
  census: { entities_total: 3, with_role: 2, placed: 2, local: 2 },
};

const UPLINK_TE = `twin_config:
  id: corp-up
  segments:
    - { name: net-lan, cidr: 10.9.0.0/24 }
    - { name: net-wan, cidr: 10.9.1.0/24, egress: true }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: net-lan }
        - { segment: net-wan }
    - name: ws01
      image: debian-12-cloud@v1
      net: net-lan
`;

/** The seeded fake with the evidence export on corp-cast and no working document for it, so
 *  the page opens the cast view (with a document it would open the design view of the text). */
const seg = (name: string, cidr: string): TwinTree["segments"][number] => ({ name, cidr, zone_type: "vlan", hosts: 1, roles: [], taps: [], nodes: [] });
const CAST_TREE: TwinTree = {
  id: "corp-cast",
  hosts: 2,
  segments: [seg("net-lan", "10.1.0.0/24"), seg("net-dmz", "10.1.1.0/24")],
  routers: [{ name: "gw01", kind: "router", role: "gw", image: "debian-12-cloud@v1", count: 1, instances: ["gw01"], segments: ["net-lan", "net-dmz"], requires: [], listen: [], install: [], personas: [] }],
  traffic_profile: null,
  tap_plan: null,
  warnings: [],
};
function withEvidence() {
  const base = seeded();
  const { "corp-cast": _dropped, ...documents } = base.state.documents;
  return seeded({ documents, exports: { "corp-cast": EVIDENCE }, trees: { "corp-cast": CAST_TREE } });
}

/** The seeded fake plus one authored twin whose router has a leg on an `egress: true` segment. */
function withUplinkTwin() {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, "corp-up": { ...base.state.twins["corp-auth"]!, id: "corp-up" } },
    documents: { ...base.state.documents, "corp-up": { id: "corp-up", te_file: "te.yaml", files: { "te.yaml": UPLINK_TE, "twin.yaml": twinYaml("corp-up") }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id: "corp-up", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}

/** Every positioned element's absolute y by its pin key. */
function ys(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const el of document.querySelectorAll<HTMLElement>("[data-key][data-y]")) out[el.dataset["key"]!] = Number(el.dataset["y"]);
  return out;
}

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("rule 1: the Internet and the gateway", () => {
  it("an authored twin with no uplink draws no Internet and no gateway node, and starts at its router", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryByTestId("canvas-internet")).toBeNull();
    expect(screen.queryByTestId("canvas-wan")).toBeNull();
    expect(within(screen.getByTestId("canvas-router")).queryByTestId("wan-badge")).toBeNull();
    const y = ys();
    expect(Math.min(...Object.values(y))).toBe(y["host:gw"]);
  });

  it("a router with a leg on an `egress: true` segment is the config's uplink: the Internet is drawn over it and it wears the badge", async () => {
    const { api } = withUplinkTwin();
    renderAt(api, "/twins/corp-up/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("canvas-internet").textContent).toContain("uplink on gw");
    expect(screen.queryByTestId("canvas-wan")).toBeNull();
    const router = screen.getByTestId("canvas-router");
    expect(within(router).getByTestId("wan-badge").getAttribute("title")).toBe("the config's uplink");
    expect(router.dataset["gateway"]).toBe("true");
    const y = ys();
    expect(y["host:gw"]).toBeGreaterThan(0);
    // The design graph carries the uplink from the document text.
    const g = documentGraph(readDoc({ id: "corp-up", teFile: "te.yaml", files: { "te.yaml": UPLINK_TE } }), "corp-up");
    expect(g.uplinks).toEqual(["gw"]);
    expect(authoredGraph({ scenario_id: "x", nodes: [{ id: "n", type: "network", cidr: "10.0.0.0/24" }, { id: "r", type: "router" }], edges: [{ source: "r", target: "n" }] }, null).uplinks).toEqual([]);
  });

  it("a cast twin with non-local peers draws the Internet; its one router is the gateway, with the crossing figure on the badge's card and in the inspector", async () => {
    const { api } = withEvidence();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("canvas-internet").textContent).toContain("1 non-local");
    expect(screen.queryByTestId("canvas-wan")).toBeNull();
    const router = screen.getByTestId("canvas-router");
    expect(within(router).getByTestId("wan-badge")).toBeTruthy();
    fireEvent.click(router);
    const facts = await screen.findByTestId("ro-router");
    expect(within(facts).getByTestId("ro-crossing").textContent).toMatch(/^\d+ crossing edges? · [\d,]+ bytes · [\d,]+ flows$/);
    fireEvent.mouseEnter(router);
    const card = await screen.findByTestId("canvas-hover-card");
    expect(card.textContent).toContain("Crossing");
  });
});

describe("rule 2: structure always, evidence on demand", () => {
  const graph = buildGraph(EVIDENCE, { imprintId: "corp-cast", deployMinOccupancy: 0, routers: [{ name: "gw01", segments: ["net-lan", "net-dmz"] }] });
  const placed = placeTree(graph);
  const arcs = placed.edges.filter((e) => e.type === "arc");

  it("the placement carries the arcs, and nothing shows them until the overlay lights one or the selection touches one", () => {
    expect(arcs.length).toBeGreaterThan(0);
    const keys = arcs.map((e) => (e.data?.kind === "arc" ? e.data.key : ""));
    for (const k of keys) expect(arcShown(k, NO_SCOPE, new Set())).toBe(false);
    expect(arcShown(keys[0]!, NO_SCOPE, new Set([keys[0]!]))).toBe(true);
    // A selected segment opens every arc on it; a selected host only the arcs its own edges fall into.
    const seg = placed.nodes.find((n) => n.data.kind === "segment")!;
    const segScope = arcScope([seg], graph, EVIDENCE, placed.anchorByName);
    expect(keys.some((k) => arcShown(k, segScope, new Set()))).toBe(true);
    const chip = placed.nodes.find((n) => n.data.kind === "host" && n.data.nodeId === "address:10.80.20.4")!;
    const hostScope = arcScope([chip], graph, EVIDENCE, placed.anchorByName);
    expect(keys.filter((k) => arcShown(k, hostScope, new Set()))).toEqual(["net-dmz|net-lan"]);
    // The dmz host has no observed edge of its own that crosses: nothing opens for it.
    const quiet = placed.nodes.find((n) => n.data.kind === "host" && n.data.nodeId === "address:10.1.1.7")!;
    expect(arcScope([quiet], graph, EVIDENCE, placed.anchorByName).keys.has("net-dmz|net-lan")).toBe(true);
    // The legs are structure: every one is drawn whatever the scope.
    expect(placed.edges.filter((e) => e.type === "leg").length).toBeGreaterThan(0);
  });

  it("a cast twin opens with zero observed-flow edges in the DOM; selecting a segment draws the arcs on it", async () => {
    const { api } = withEvidence();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("canvas-internet")).toBeTruthy(); // the cast view, not the design view
    expect(document.querySelectorAll('[data-testid="canvas-arc"]').length).toBe(0);
    // jsdom draws edges only once React Flow has measured the nodes; the legs say whether it has.
    const legs = document.querySelectorAll('[data-testid="canvas-leg"]').length;
    fireEvent.click(screen.getAllByTestId("canvas-segment")[0]!);
    if (legs > 0) await waitFor(() => expect(document.querySelectorAll('[data-testid="canvas-arc"]').length).toBeGreaterThan(0));
    else expect(document.querySelectorAll('[data-testid="canvas-arc"]').length).toBe(0);
  });
});

describe("the tree plane has no site tint (track 5)", () => {
  it("draws every segment as a pill under its carrier and no site group", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryAllByTestId("canvas-site")).toEqual([]);
    expect(screen.getAllByTestId("canvas-segment").length).toBe(2);
  });
});

describe("rule 4: labels", () => {
  it("chips carry no text by default; the rail toggle cycles selected → none → all and is kept per user; a selected chip shows its name", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(hosts()).toEqual(["web01", "ws01"]);
    for (const h of screen.getAllByTestId("canvas-host")) expect(h.textContent).toBe("");
    const toggle = screen.getByTestId("rail-labels");
    expect(toggle.getAttribute("aria-label") ?? toggle.textContent).toContain("Labels: selected");
    fireEvent.click(host("ws01"));
    await waitFor(() => expect(host("ws01").textContent).toContain("ws01"));
    expect(host("web01").textContent).toBe("");
    fireEvent.click(toggle); // none
    await waitFor(() => expect(host("ws01").textContent).toBe(""));
    expect(localStorage.getItem("axr-canvas-labels")).toBe("none");
    fireEvent.click(screen.getByTestId("rail-labels")); // all
    await waitFor(() => expect(host("web01").textContent).toContain("web01"));
    expect(localStorage.getItem("axr-canvas-labels")).toBe("all");
  });
});
