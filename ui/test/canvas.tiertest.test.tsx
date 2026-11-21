/**
 * Regressions seen on /canvas/tier-test (2026-11-20), on a tier-test-shaped authored twin: its
 * conduits are implemented by CHAINS of routers (users-a -> dist-a -> core -> srv), so none draws as
 * a warning; a catalog twin opens in the tree, and in free mode every host keeps its access edge;
 * conduits are decluttered (drawn on the toggle, a selection, or a finding).
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { conduitVisible } from "../src/features/edit-canvas/Canvas";
import { freeLayout, FREE_FAN } from "../src/features/edit-canvas/canvas/free";
import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { conduitImplemented, routerComponents } from "../src/features/edit-canvas/edit/te";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { DEPLOY, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

/** tier-test's structure (catalog/twins/tier-test in the user's checkout): four routers in a tree,
 *  three transits, five LANs, the conduits its README declares. */
const TIER = `twin_config:
  id: tier-test
  segments:
    - { name: inet, cidr: 100.66.0.0/24, zone_type: vlan }
    - { name: dmz, cidr: 10.74.10.0/24, zone_type: vlan }
    - { name: srv, cidr: 10.74.20.0/24, zone_type: vlan }
    - { name: users-a, cidr: 10.74.31.0/24, zone_type: vlan }
    - { name: users-b, cidr: 10.74.41.0/24, zone_type: vlan }
    - { name: lab-b, cidr: 10.74.42.0/24, zone_type: vlan }
    - { name: t-edge, cidr: 10.74.0.0/29, zone_type: vlan }
    - { name: t-a, cidr: 10.74.0.8/29, zone_type: vlan }
    - { name: t-b, cidr: 10.74.0.16/29, zone_type: vlan }
  nodes:
    - { name: edge, kind: router, role: gw, image: debian-12-cloud@v1, nets: [{ segment: inet }, { segment: dmz }, { segment: t-edge }] }
    - { name: core, kind: router, role: gw, image: debian-12-cloud@v1, nets: [{ segment: t-edge }, { segment: t-a }, { segment: t-b }, { segment: srv }] }
    - { name: dist-a, kind: router, role: gw, image: debian-12-cloud@v1, nets: [{ segment: t-a }, { segment: users-a }] }
    - { name: dist-b, kind: router, role: gw, image: debian-12-cloud@v1, nets: [{ segment: t-b }, { segment: users-b }, { segment: lab-b }] }
    - { name: ext-web, role: web-tier, image: debian-12-cloud@v1, net: inet }
    - { name: dmz-web, role: web-tier, image: debian-12-cloud@v1, net: dmz }
    - { name: srv-web, role: web-tier, image: debian-12-cloud@v1, net: srv }
    - { name: srv-file, role: file-server, image: debian-12-cloud@v1, net: srv }
    - { name: lab-web, role: web-tier, image: debian-12-cloud@v1, net: lab-b }
    - { name: cli-a, role: generic-endpoint, image: debian-12-cloud@v1, count: 3, net: users-a }
    - { name: cli-b, role: generic-endpoint, image: debian-12-cloud@v1, count: 3, net: users-b }
  conduits:
    - { from: users-a, to: srv }
    - { from: users-b, to: srv }
    - { from: users-a, to: lab-b }
    - { from: users-b, to: lab-b }
    - { from: users-a, to: inet }
    - { from: users-b, to: inet }
    - { from: inet, to: dmz }
`;
const doc = (te = TIER) => ({ id: "tier-test", teFile: "te.yaml", files: { "te.yaml": te } });

describe("conduits implemented by a chain of routers", () => {
  it("every tier-test conduit is implemented (none draws as a warning)", () => {
    const te = readDoc(doc()).te;
    const comps = routerComponents(te);
    expect(te.conduits.filter((c) => !conduitImplemented(te, c.from, c.to, comps))).toEqual([]);
  });

  it("an island no router reaches is not; a single host on both segments still is", () => {
    const island = TIER.replace("  conduits:\n", "    - { name: lone, image: x, net: island }\n  conduits:\n    - { from: srv, to: island }\n").replace("    - { name: t-b,", "    - { name: island, cidr: 10.9.9.0/24 }\n    - { name: t-b,");
    const te = readDoc(doc(island)).te;
    expect(conduitImplemented(te, "srv", "island")).toBe(false);
    expect(conduitImplemented(te, "users-a", "srv")).toBe(true);
    const dual = island.replace("    - { name: lone, image: x, net: island }", "    - { name: lone, image: x, nets: [{ segment: island }, { segment: srv }] }");
    expect(conduitImplemented(readDoc(doc(dual)).te, "srv", "island")).toBe(true);
  });
});

describe("the layout", () => {
  it("free mode draws every host's access edge, from a node that exists", () => {
    const m = readDoc(doc());
    const g = documentGraph(m, "tier-test");
    const p = freeLayout(placeTree(buildGraph(g.exportData, g.cast), {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, fan: FREE_FAN }));
    const ids = new Set(p.nodes.map((n) => n.id));
    const hosts = p.nodes.filter((n) => n.data.kind === "host");
    expect(hosts.length).toBe(7);
    for (const h of hosts) {
      const access = p.edges.filter((e) => e.target === h.id);
      expect(access.length, h.id).toBeGreaterThan(0);
      for (const e of access) expect(ids.has(e.source), `${e.id} from ${e.source}`).toBe(true);
    }
    // A router's own legs still leave the router.
    for (const e of p.edges.filter((x) => /^leg:(edge|core|dist-a|dist-b)>/.test(x.id))) expect(e.source).toMatch(/^router:/);
  });

  function withTier(over: Parameters<typeof seeded>[0] = {}) {
    const base = seeded();
    return seeded({
      twins: { ...base.state.twins, "tier-test": { ...base.state.twins["corp-auth"]!, id: "tier-test" } },
      documents: { ...base.state.documents, "tier-test": { id: "tier-test", te_file: "te.yaml", files: { "te.yaml": TIER, "twin.yaml": twinYaml("tier-test") }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "tier-test", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
      ...over,
    });
  }

  it("an authored catalog twin opens in the tree", async () => {
    const { api } = withTier();
    renderAt(api, "/twins/tier-test/canvas");
    expect((await screen.findByTestId("layout-mode")).dataset["mode"]).toBe("tree");
  });

  it("a deployed twin opens in the tree; a side-car that says free wins", async () => {
    const { api } = withTier({ deploys: [{ ...DEPLOY, scenario_id: "tier-test", deploy_id: "dep-tier" }] });
    const r = renderAt(api, "/twins/tier-test/canvas");
    expect((await screen.findByTestId("layout-mode")).dataset["mode"]).toBe("tree");
    r.unmount();
    const s = withTier();
    s.state.layouts["tier-test"] = { imprint_id: "tier-test", path: "/x", exists: true, etag: "e1", schema: "axr-layout/1", imprint: "tier-test", pins: {}, plane: "tree", annotations: [], mode: "free" } as never;
    renderAt(s.api, "/twins/tier-test/canvas");
    await waitFor(() => expect(screen.getByTestId("layout-mode").dataset["mode"]).toBe("free"));
  });
});

describe("conduits declutter", () => {
  const pairs = [{ a: "users-a", b: "srv" }];
  const base = { on: false, selectedKey: null, selectedSegments: [] as string[], findings: new Map<string, unknown>() };

  it("hidden by default; shown on the toggle, on its selection, on a selected segment, and always with a finding", () => {
    expect(conduitVisible(base, pairs, "k")).toBe(false);
    expect(conduitVisible({ ...base, on: true }, pairs, "k")).toBe(true);
    expect(conduitVisible({ ...base, selectedKey: "k" }, pairs, "k")).toBe(true);
    expect(conduitVisible({ ...base, selectedSegments: ["srv"] }, pairs, "k")).toBe(true);
    expect(conduitVisible({ ...base, selectedSegments: ["dmz"] }, pairs, "k")).toBe(false);
    expect(conduitVisible({ ...base, findings: new Map([["conduit:srv|users-a", {}]]) }, pairs, "k")).toBe(true);
  });

  it("the rail's Conduits toggle is off by default and remembered in this browser", async () => {
    const base2 = seeded();
    const { api } = seeded({
      twins: { ...base2.state.twins, "tier-test": { ...base2.state.twins["corp-auth"]!, id: "tier-test" } },
      documents: { ...base2.state.documents, "tier-test": { id: "tier-test", te_file: "te.yaml", files: { "te.yaml": TIER, "twin.yaml": twinYaml("tier-test") }, skipped: [] } },
    });
    const r = renderAt(api, "/twins/tier-test/canvas");
    const t = await screen.findByTestId("rail-conduits");
    expect(t.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(t);
    expect(screen.getByTestId("rail-conduits").getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("axr-canvas-conduits")).toBe("on");
    r.unmount();
    renderAt(api, "/twins/tier-test/canvas");
    expect((await screen.findByTestId("rail-conduits")).getAttribute("aria-pressed")).toBe("true");
  });
});
