/**
 * C2 on the design canvas (`ui/src/features/edit-canvas/SPEC.md`, Phase 1):
 * the authored and the cast graph render with every segment, host and router
 * present; positions are stable across two renders of one layout document; a
 * `host:` pin is honoured; the traffic overlay lights the matching
 * attachments; over the element budgets the page falls back to the list.
 *
 * jsdom measures nothing, so every position assertion reads the placement
 * the nodes carry (`data-x` / `data-y`), never a pixel.
 */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LayoutDoc, Topology, TopologyExport, TwinTree } from "../src/api/types";
import { absolutePosition, pinKeyOf } from "../src/features/edit-canvas/canvas/placement";
import { CANVAS_PLANE } from "../src/features/edit-canvas/canvas/plane";
import { fakeApi } from "./helpers/fakeApi";
import { twin } from "./imprints.page.test";
import { renderAt } from "./helpers/render";

// ---------------------------------------------------------------------------
// Fixtures: a small authored twin and a small cast twin.
// ---------------------------------------------------------------------------
const AUTHORED: Topology = {
  scenario_id: "corp-auth",
  nodes: [
    { id: "net-lan", type: "network", cidr: "10.1.0.0/24" },
    { id: "net-dmz", type: "network", cidr: "10.1.1.0/24" },
    { id: "ws01", type: "host", image: "img-a" },
    { id: "db01", type: "host", image: "img-b" },
    { id: "web01", type: "host", image: "img-c" },
    { id: "gw", type: "router", image: "img-r" },
  ],
  edges: [
    { source: "ws01", target: "net-lan", ip: "10.1.0.10" },
    { source: "db01", target: "net-lan", ip: "10.1.0.12" },
    { source: "web01", target: "net-dmz", ip: "10.1.1.20" },
    { source: "web01", target: "net-lan", ip: "10.1.0.11" },
    { source: "gw", target: "net-lan", ip: "10.1.0.1" },
    { source: "gw", target: "net-dmz", ip: "10.1.1.1" },
  ],
};

function node(name: string, role: string | null, segments: string[], instances: string[] = [name]): TwinTree["routers"][number] {
  return { name, kind: segments.length > 1 && name.startsWith("gw") ? "router" : "host", role, image: "img", count: instances.length, instances, segments, requires: [], listen: role === "web-tier" ? [{ port: 443, proto: "tcp" }] : [], install: [], personas: [] };
}

const AUTHORED_TREE: TwinTree = {
  id: "corp-auth",
  hosts: 3,
  segments: [
    { name: "net-lan", cidr: "10.1.0.0/24", zone_type: "simple", hosts: 2, roles: [], taps: [], nodes: [node("ws01", "corp-workstation", ["net-lan"]), node("db01", "db-tier", ["net-lan"])] },
    { name: "net-dmz", cidr: "10.1.1.0/24", zone_type: "simple", hosts: 1, roles: [], taps: [], nodes: [node("web01", "web-tier", ["net-dmz", "net-lan"])] },
  ],
  routers: [node("gw", null, ["net-lan", "net-dmz"])],
  warnings: [],
};

function exportNode(address: string, role: string | null, segment: string | null, over: Partial<TopologyExport["nodes"][number]> = {}): TopologyExport["nodes"][number] {
  return {
    id: `address:${address}`, address, locality: "local", segment, segment_evidence_class: segment ? "observed" : null,
    role, role_confidence: role ? 0.9 : null, role_evidence_class: role ? "observed" : null, windows_seen: 10, listen_ports: [], dial_ports: [], ...over,
  };
}

const CAST: TopologyExport = {
  imprint_id: "corp-cast", pit_id: "pit-1", as_of: 1791313200, network: "corp", vantage: "hq/rdr1:12", windows: 10, schema_version: "1",
  segments: [
    { cidr: "10.80.20.0/24", name: "seg-a", entities: 2, coverage_source: null },
    { cidr: "10.80.30.0/24", name: "seg-b", entities: 1, coverage_source: null },
    { cidr: "10.20.5.0/24", name: "seg-c", entities: 1, coverage_source: null },
  ],
  nodes: [
    exportNode("10.80.20.4", "domain-controller", "10.80.20.0/24", { listen_ports: ["tcp/389", "udp/53"] }),
    exportNode("10.80.20.9", "corp-workstation", "10.80.20.0/24", { segment_evidence_class: "imputed", role_evidence_class: "imputed", role_confidence: 0.4 }),
    exportNode("10.80.30.7", "web-tier", "10.80.30.0/24", { listen_ports: ["tcp/443"] }),
    exportNode("10.20.5.3", null, "10.20.5.0/24", { windows_seen: 2 }),
    exportNode("8.8.8.8", null, null, { locality: "external", segment_evidence_class: null, windows_seen: null }),
  ],
  edges: [
    { src: "address:10.80.20.9", dst: "address:10.80.30.7", proto: "tcp", bytes: 500000, flows: 40, evidence_class: "observed" },
    { src: "address:10.80.20.4", dst: "address:10.20.5.3", proto: "udp", bytes: 2000, flows: 3, evidence_class: "observed" },
    { src: "address:10.80.20.9", dst: "address:8.8.8.8", proto: "tcp", bytes: 999, flows: 1, evidence_class: "observed" },
  ],
  census: { entities_total: 5, with_role: 3, placed: 4, local: 4 },
};

const CAST_TREE: TwinTree = {
  id: "corp-cast",
  hosts: 4,
  segments: [
    { name: "seg-a", cidr: "10.200.20.0/24", zone_type: null, hosts: 2, roles: [], taps: [], nodes: [] },
    { name: "seg-b", cidr: "10.200.30.0/24", zone_type: null, hosts: 1, roles: [], taps: [], nodes: [] },
    { name: "seg-c", cidr: "10.200.5.0/24", zone_type: null, hosts: 1, roles: [], taps: [], nodes: [] },
  ],
  routers: [node("gw01", null, ["seg-a", "seg-b"])],
  warnings: [],
};

function layoutDoc(id: string, pins: LayoutDoc["pins"], plane: string | null = CANVAS_PLANE): LayoutDoc {
  return { imprint_id: id, path: `/store/twins/${id}/generated/layout.json`, exists: true, etag: "etag-1", schema: "axr-layout/1", imprint: id, pins, plane, annotations: [] };
}

function seeded(over: Parameters<typeof fakeApi>[0] = {}) {
  return fakeApi({
    twins: { "corp-auth": twin("corp-auth", { hosts: 3, segments: 2 }), "corp-cast": twin("corp-cast", { hosts: 4, segments: 3, pit_id: "pit-1" }) },
    trees: { "corp-auth": AUTHORED_TREE, "corp-cast": CAST_TREE },
    topologies: { "corp-auth": AUTHORED },
    exports: { "corp-cast": CAST },
    ...over,
  });
}

/** Every placed element's absolute position, by its pin key. */
function positions(): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  for (const el of document.querySelectorAll<HTMLElement>("[data-key][data-x]")) {
    out[el.dataset["key"]!] = [Number(el.dataset["x"]), Number(el.dataset["y"])];
  }
  return out;
}

afterEach(() => {
  vi.useRealTimers();
  delete document.documentElement.dataset["theme"];
});

describe("the design canvas (C2, read-only)", () => {
  it("renders an authored twin with every segment, host and router, roles from the tree", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    const segments = await screen.findAllByTestId("canvas-segment");
    expect(segments.map((s) => s.dataset["cidr"]).sort()).toEqual(["10.1.0.0/24", "10.1.1.0/24"]);
    const hosts = screen.getAllByTestId("canvas-host");
    expect(hosts.map((h) => h.dataset["nodeId"]).sort()).toEqual(["db01", "web01", "ws01"]);
    expect(screen.getByTestId("canvas-router").textContent).toContain("gw");
    expect(screen.getByTestId("canvas-router").textContent).toContain("2 legs");
    // The multi-homed host sits in its FIRST segment with the address it holds there.
    const web = hosts.find((h) => h.dataset["nodeId"] === "web01")!;
    expect(web.textContent).toContain("10.1.1.20");
    expect(web.textContent).toContain("WEB");
    expect(screen.getByText("authored")).toBeTruthy();
    expect(screen.getByText(/no observed evidence, no traffic to overlay/)).toBeTruthy();
  });

  it("renders a cast twin with its evidence rings, role badges, ghosts and the router from the tree", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast/canvas");
    const segments = await screen.findAllByTestId("canvas-segment");
    expect(segments.map((s) => s.dataset["cidr"]).sort()).toEqual(["10.20.5.0/24", "10.80.20.0/24", "10.80.30.0/24"]);
    const hosts = screen.getAllByTestId("canvas-host");
    expect(hosts.length).toBe(4);
    const dc = hosts.find((h) => h.dataset["nodeId"] === "address:10.80.20.4")!;
    expect(dc.className).toContain("ev-observed");
    expect(dc.textContent).toContain("DC");
    const ws = hosts.find((h) => h.dataset["nodeId"] === "address:10.80.20.9")!;
    expect(ws.className).toContain("ev-imputed");
    expect(screen.getByTestId("canvas-router").textContent).toContain("gw01");
    expect(screen.getByTestId("canvas-internet").textContent).toContain("1 non-local");
    expect(screen.getByTestId("canvas-wan").textContent).toContain("Gateway gw01");
    expect(screen.getAllByTestId("canvas-site").length).toBe(2);
    expect(screen.getByText("cast · observed evidence")).toBeTruthy();
  });

  it("places a cast twin identically across two renders of the same layout document", async () => {
    const doc = layoutDoc("corp-cast", { "segment:10.80.30.0/24": { x: 700, y: 650 }, "site:misc": { x: 1200, y: 300 } });
    const first = seeded({ layouts: { "corp-cast": doc } });
    const r1 = renderAt(first.api, "/twins/corp-cast/canvas");
    // The layout document loads beside the graph; the pins land when it does.
    await waitFor(() => expect(positions()["segment:10.80.30.0/24"]).toEqual([700, 650]));
    const a = positions();
    r1.unmount();
    const second = seeded({ layouts: { "corp-cast": doc } });
    renderAt(second.api, "/twins/corp-cast/canvas");
    await waitFor(() => expect(positions()["segment:10.80.30.0/24"]).toEqual([700, 650]));
    const b = positions();
    expect(Object.keys(a).length).toBeGreaterThan(5);
    expect(b).toEqual(a);
    // The pins are honoured as absolute top-lefts; unpinned elements keep their computed slots.
    expect(a["segment:10.80.30.0/24"]).toEqual([700, 650]);
    expect(a["site:misc"]).toEqual([1200, 300]); // the lone 10.20 segment is the engine's misc group
    expect(a["site:10.80"]).not.toEqual([1200, 300]);
    // A host chip follows its (unpinned) segment: it sits inside the segment's box.
    const [sx, sy] = a["segment:10.80.20.0/24"]!;
    const [hx, hy] = a["host:address:10.80.20.4"]!;
    expect(hx).toBeGreaterThan(sx);
    expect(hy).toBeGreaterThan(sy);
  });

  it("honours a host: pin and ignores pins authored on another plane, saying so", async () => {
    const pinned = seeded({ layouts: { "corp-cast": layoutDoc("corp-cast", { "host:address:10.80.20.4": { x: 333, y: 444 } }) } });
    const r = renderAt(pinned.api, "/twins/corp-cast/canvas");
    await waitFor(() => expect(positions()["host:address:10.80.20.4"]).toEqual([333, 444]));
    expect(screen.queryByText(/another plane/)).toBeNull();
    r.unmount();

    const foreign = seeded({ layouts: { "corp-cast": layoutDoc("corp-cast", { "host:address:10.80.20.4": { x: 333, y: 444 } }, "l0-3-deadbeef") } });
    renderAt(foreign.api, "/twins/corp-cast/canvas");
    await screen.findByText(/drawn on another plane \(l0-3-deadbeef\) and are not applied/);
    expect(positions()["host:address:10.80.20.4"]).not.toEqual([333, 444]);
  });

  it("lights the attachments matching a traffic filter for a timeout, then fades them", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("Protocol"), { target: { value: "tcp" } });
    fireEvent.change(screen.getByLabelText("Glow seconds"), { target: { value: "5" } });
    fireEvent.submit(screen.getByRole("form", { name: "Traffic overlay" }));
    const lit = () => screen.getAllByTestId("canvas-host").filter((h) => h.className.includes("glow")).map((h) => h.dataset["nodeId"]).sort();
    expect(lit()).toEqual(["address:10.80.20.9", "address:10.80.30.7"]);
    expect(screen.getByTestId("overlay-note").textContent).toBe("2 of 3 observed edges match · 3 hosts lit for 5s");
    act(() => {
      vi.advanceTimersByTime(5100);
    });
    expect(lit()).toEqual([]);
    expect(screen.getByTestId("overlay-note").textContent).toContain("faded");
    vi.useRealTimers();

    // A port filter matches an endpoint's own claim; a peer filter a name or address.
    fireEvent.change(screen.getByLabelText("Protocol"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Port"), { target: { value: "443" } });
    fireEvent.submit(screen.getByRole("form", { name: "Traffic overlay" }));
    expect(lit()).toEqual(["address:10.80.20.9", "address:10.80.30.7"]);
    fireEvent.change(screen.getByLabelText("Port"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Peer"), { target: { value: "10.20.5" } });
    fireEvent.submit(screen.getByRole("form", { name: "Traffic overlay" }));
    expect(lit()).toEqual(["address:10.20.5.3", "address:10.80.20.4"]);
  });

  it("finds a node by address and centres on it; the hover card names address, role and evidence", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.change(screen.getByLabelText("Find node"), { target: { value: "10.80.30.7" } });
    fireEvent.click(screen.getByRole("button", { name: "Find" }));
    expect(screen.getByText("host address:10.80.30.7")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "address:10.80.30.7")!.className).toContain("selected"));
    fireEvent.change(screen.getByLabelText("Find node"), { target: { value: "10.9.9.0/24" } });
    fireEvent.click(screen.getByRole("button", { name: "Find" }));
    expect(screen.getByText("no segment 10.9.9.0/24 in this twin")).toBeTruthy();

    fireEvent.mouseEnter(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "address:10.80.20.9")!);
    const card = screen.getByTestId("canvas-hover-card");
    expect(card.textContent).toContain("10.80.20.9");
    expect(card.textContent).toContain("corp-workstation (0.40)");
    expect(card.textContent).toContain("imputed");
    expect(card.textContent).toContain("Occupancy1.00");
  });

  it("locks and unlocks dragging, and follows the app theme", async () => {
    document.documentElement.dataset["theme"] = "dark";
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    const canvas = screen.getByTestId("canvas");
    expect(canvas.dataset["colorMode"]).toBe("dark");
    expect(canvas.dataset["locked"]).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Lock all" }));
    expect(canvas.dataset["locked"]).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Unlock all" }));
    expect(canvas.dataset["locked"]).toBe("false");
  });

  it("falls back to the list and the role matrix over the element budget, never a denser canvas", async () => {
    // Twelve sites of five segments: every group is within its own budget, so
    // nothing collapses, and 60 tiles exceed the level-0 box budget of 48.
    const segments: TopologyExport["segments"] = [];
    const nodes: TopologyExport["nodes"] = [];
    for (let s = 0; s < 12; s++) {
      for (let k = 0; k < 5; k++) {
        const cidr = `10.${s + 1}.${k + 1}.0/24`;
        segments.push({ cidr, name: `seg-${s}-${k}`, entities: 1, coverage_source: null });
        nodes.push(exportNode(`10.${s + 1}.${k + 1}.9`, k ? "corp-workstation" : "domain-controller", cidr));
      }
    }
    const big: TopologyExport = { ...CAST, imprint_id: "big", segments, nodes, edges: [{ src: nodes[0]!.id, dst: nodes[7]!.id, proto: "tcp", bytes: 10, flows: 1, evidence_class: "observed" }], census: { entities_total: 60, with_role: 60, placed: 60, local: 60 } };
    const { api } = seeded({ twins: { big: twin("big", { hosts: 60, segments: 60 }) }, exports: { big }, trees: {} });
    renderAt(api, "/twins/big/canvas");
    const fallback = await screen.findByTestId("canvas-fallback");
    expect(fallback.textContent).toContain("62 boxes exceed the canvas budget of 48");
    expect(fallback.textContent).toContain("Showing the list and the role matrix instead");
    expect(screen.queryByTestId("canvas-segment")).toBeNull();
    expect(screen.getByTestId("canvas-matrix").textContent).toContain("domain-controller");
    await screen.findByTestId("twin-tree");
  });

  it("links from the twin page to its canvas", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-cast");
    await screen.findByTestId("twin-summary");
    expect(screen.getByRole("link", { name: "Open the design canvas" }).getAttribute("href")).toBe("/canvas/corp-cast");
  });
});

describe("the one write: a dragged node's position as a pin", () => {
  it("keys sites, segments and nodes; chrome, collapsed bands and +N tiles are not pinnable", () => {
    expect(pinKeyOf({ data: { kind: "site", key: "site:10.80", label: "10.80", shown: 1, total: 1, northSouthBytes: 0 } })).toBe("site:10.80");
    expect(pinKeyOf({ data: { kind: "segment", name: "seg-a", cidr: "10.80.20.0/24", deployedCidr: null, zoneType: null, hosts: 0, ghosts: 0, evidenceMix: [], roleBadges: [], hasRouterLeg: false } })).toBe("segment:10.80.20.0/24");
    expect(pinKeyOf({ data: { kind: "router", name: "gw01", segments: [], drawnLegs: 0 } })).toBe("host:gw01");
    expect(pinKeyOf({ data: { kind: "overflow", siteKey: "site:10.80", hidden: 3, total: 9, expanded: false } })).toBeNull();
    expect(pinKeyOf({ data: { kind: "internet", label: "", sub: "" } })).toBeNull();
  });

  it("writes the ABSOLUTE top-left: a chip's position plus its segment's plus its site's", () => {
    const nodes = [
      { id: "site:10.80", position: { x: 100, y: 200 } },
      { id: "seg:seg-a", position: { x: 12, y: 38 }, parentId: "site:10.80" },
      { id: "host:address:10.80.20.4", position: { x: 8, y: 34 }, parentId: "seg:seg-a" },
    ];
    expect(absolutePosition(nodes, "host:address:10.80.20.4")).toEqual({ x: 120, y: 272 });
    expect(absolutePosition(nodes, "seg:seg-a")).toEqual({ x: 112, y: 238 });
    expect(absolutePosition(nodes, "nope")).toBeNull();
  });

  it("PUTs the document's pins of this plane plus the dragged one, stamped, with the etag as precondition", async () => {
    const { nextLayoutWrite } = await import("../src/features/edit-canvas/CanvasPage");
    const mine = layoutDoc("corp-cast", { "site:misc": { x: 1200, y: 300 } });
    expect(nextLayoutWrite(mine, "corp-cast", "host:address:10.80.20.4", { x: 1, y: 2 })).toEqual({
      schema: "axr-layout/1", imprint: "corp-cast", plane: CANVAS_PLANE, etag: "etag-1",
      pins: { "site:misc": { x: 1200, y: 300 }, "host:address:10.80.20.4": { x: 1, y: 2 } },
    });
    // Another plane's pins are not re-saved as this drawing's; a first save has no etag.
    const foreign = layoutDoc("corp-cast", { "site:10.20": { x: 9, y: 9 } }, "l0-3-deadbeef");
    expect(nextLayoutWrite(foreign, "corp-cast", "segment:10.80.20.0/24", { x: 5, y: 6 }).pins).toEqual({ "segment:10.80.20.0/24": { x: 5, y: 6 } });
    expect(nextLayoutWrite(null, "corp-cast", "segment:10.80.20.0/24", { x: 5, y: 6 }).etag).toBeNull();

    // The fake control plane guards the write the way the real one does.
    const { api, state } = seeded({ layouts: { "corp-cast": mine } });
    const saved = await api.putLayout("corp-cast", nextLayoutWrite(mine, "corp-cast", "host:address:10.80.20.4", { x: 1, y: 2 }));
    expect(saved.pins["host:address:10.80.20.4"]).toEqual({ x: 1, y: 2 });
    expect(state.writes.at(-1)).toMatchObject({ op: "put-layout", id: "corp-cast" });
    await expect(api.putLayout("corp-cast", nextLayoutWrite(mine, "corp-cast", "host:x", { x: 0, y: 0 }))).rejects.toMatchObject({ status: 412 });
  });
});
