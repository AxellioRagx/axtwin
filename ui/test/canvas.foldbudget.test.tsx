/**
 * The fold budget (review R5-2, 2026-11-08), over the real corp-twin bundle: every site pill a
 * router serves is always on its rail and "+N more" holds only loose segments beyond the rail's
 * Fan-out; an open site shows a budget of its segments per page, its own "+N more" last on its
 * row; the "+N more" pill, its "fold N back" and the status strip count the same segments; and a
 * fold's refit frames the router plus the row, never below 50 % zoom.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FOLD_FIT_MIN_ZOOM, boundsOf, foldFitTargets, foldViewport } from "../src/features/edit-canvas/canvas/fit";
import { ID, type CanvasNode } from "../src/features/edit-canvas/canvas/placement";
import { TREE_FAN_DEFAULT, TREE_ID, placeTree, sitePageKey, sitePages, withoutFold } from "../src/features/edit-canvas/canvas/tree";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const BUNDLE = resolve(__dirname, "../../catalog/twins/corp-twin");
const TE = readFileSync(resolve(BUNDLE, "te.yaml"), "utf8");
const ROUTER = "hq-mx";
const SITES = ["10.120", "10.180", "10.192", "10.199"];
const S180 = `site:${ROUTER}|10.180`;

const docGraph = () => documentGraph(readDoc({ id: "corp-twin", teFile: "te.yaml", files: { "te.yaml": TE } }), "corp-twin");
const corpGraph = () => {
  const g = docGraph();
  return buildGraph(g.exportData, g.cast);
};
function corp(expanded: string[] = []) {
  const g = docGraph();
  return placeTree(buildGraph(g.exportData, g.cast), {}, { extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree, expanded });
}
const overflows = (p: ReturnType<typeof corp>) => p.nodes.filter((n): n is CanvasNode & { data: { kind: "overflow"; siteKey: string; hidden: number; expanded: boolean } } => n.data.kind === "overflow");
const segmentsIn = (ids: readonly string[]) => ids.filter((id) => id.startsWith(ID.segment("")));

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("corp-twin's rail, collapsed", () => {
  const p = corp();
  const rail = p.rows?.[ID.router(ROUTER)] ?? [];
  it("carries all four site pills, the loose pills up to the budget, and +N more last", () => {
    expect(p.overBudget).toBeNull();
    for (const site of SITES) expect(rail).toContain(TREE_ID.siteFold(ROUTER, site));
    expect(segmentsIn(rail).length).toBe(TREE_FAN_DEFAULT);
    expect(rail.at(-1)).toBe(TREE_ID.carrierOverflow(ROUTER));
  });
  it("+N more holds only loose segments: none of a site's", () => {
    const behind = Object.entries(p.anchorByName).filter(([, a]) => a === TREE_ID.carrierOverflow(ROUTER)).map(([n]) => n);
    expect(behind.length).toBeGreaterThan(0);
    const cidrOf = new Map(corpGraph().segments.map((sg) => [sg.name, sg.observedCidr]));
    for (const name of behind) expect(SITES).not.toContain(cidrOf.get(name)!.split(".").slice(0, 2).join("."));
  });
  it("the +N more pill, the status strip's count and the opened pill's fold-back agree", () => {
    const more = overflows(p).find((n) => n.id === TREE_ID.carrierOverflow(ROUTER))!;
    expect(more.data.hidden).toBe(p.hiddenSegments);
    const opened = corp([`carrier:${ROUTER}`]);
    const tile = overflows(opened).find((n) => n.data.siteKey === `carrier:${ROUTER}` && n.data.expanded)!;
    expect(tile.data.hidden).toBe(more.data.hidden);
  });
});

describe("corp-twin's 10.180, opened", () => {
  it("once: its row holds the budget, then its own +N more; twice: the next budget", () => {
    const once = corp([S180]);
    const tile = overflows(once).find((n) => n.data.siteKey === S180 && n.data.expanded)!;
    const row = once.rows?.[tile.id] ?? [];
    expect(segmentsIn(row).length).toBe(TREE_FAN_DEFAULT);
    expect(row.at(-1)).toBe(TREE_ID.rowOverflow(S180));
    const more = overflows(once).find((n) => n.id === TREE_ID.rowOverflow(S180))!;
    expect(more.data).toMatchObject({ siteKey: sitePageKey(S180, 2), hidden: 24 - TREE_FAN_DEFAULT, expanded: false });
    // Every site pill is still on the rail, and the strip counts both +N more pills.
    const rail = once.rows?.[ID.router(ROUTER)] ?? [];
    for (const site of SITES.filter((s) => s !== "10.180")) expect(rail).toContain(TREE_ID.siteFold(ROUTER, site));
    const plus = overflows(once).filter((n) => n.id === TREE_ID.carrierOverflow(ROUTER) || n.id === TREE_ID.rowOverflow(S180));
    expect(plus.length).toBe(2);
    expect(once.hiddenSegments).toBe(plus.reduce((sum, n) => sum + n.data.hidden, 0));
    // The row sits below the rail, its boxes in one line.
    const ys = row.map((id) => once.nodes.find((n) => n.id === id)!).map((n) => Math.round(n.position.y + (n.height ?? 0) / 2));
    expect(new Set(ys).size).toBe(1);
    expect(ys[0]).toBeGreaterThan(tile.position.y + (tile.height ?? 0));

    const twice = corp([S180, sitePageKey(S180, 2)]);
    const row2 = twice.rows?.[tile.id] ?? [];
    expect(segmentsIn(row2).length).toBe(2 * TREE_FAN_DEFAULT);
    expect(overflows(twice).find((n) => n.id === TREE_ID.rowOverflow(S180))!.data).toMatchObject({ siteKey: sitePageKey(S180, 3), hidden: 24 - 2 * TREE_FAN_DEFAULT });
  });
  it("the pages count in turn, and closing the site closes its pages", () => {
    expect(sitePages(new Set([S180]), S180)).toBe(1);
    expect(sitePages(new Set([S180, sitePageKey(S180, 2), sitePageKey(S180, 3)]), S180)).toBe(3);
    expect(sitePages(new Set([S180, sitePageKey(S180, 3)]), S180)).toBe(1);
    expect(withoutFold([S180, sitePageKey(S180, 2), "carrier:x", `${S180}0`], S180)).toEqual(["carrier:x", `${S180}0`]);
  });
});

describe("a fold's refit", () => {
  it("frames the router and the open row; the rail when a site closes", () => {
    const once = corp([S180]);
    const tile = overflows(once).find((n) => n.data.siteKey === S180 && n.data.expanded)!;
    expect(foldFitTargets(once, sitePageKey(S180, 2))).toEqual([ID.router(ROUTER), tile.id, ...(once.rows?.[tile.id] ?? [])]);
    const closed = corp();
    expect(foldFitTargets(closed, S180)).toEqual([ID.router(ROUTER), ...(closed.rows?.[ID.router(ROUTER)] ?? [])]);
  });
  it("fits and centres when it can at 50 % or more; past that, 50 % from the row's left edge", () => {
    const view = { width: 1600, height: 900 };
    const small = foldViewport({ x: 100, y: 0, w: 1200, h: 300 }, view);
    expect(small.zoom).toBeGreaterThanOrEqual(FOLD_FIT_MIN_ZOOM);
    expect(Math.round(small.x + (100 + 600) * small.zoom)).toBe(800);
    const wide = foldViewport({ x: 100, y: 0, w: 6000, h: 300 }, view);
    expect(wide.zoom).toBe(FOLD_FIT_MIN_ZOOM);
    const left = wide.x + 100 * wide.zoom;
    expect(left).toBeGreaterThan(0);
    expect(left).toBeLessThan(view.width * 0.15);
  });
  it("corp-twin's 10.180 opened once frames at 50 % or more", () => {
    const once = corp([S180]);
    const ids = foldFitTargets(once, S180)!;
    const b = boundsOf(once.nodes, ids)!;
    expect(foldViewport(b, { width: 1622, height: 900 }).zoom).toBeGreaterThanOrEqual(FOLD_FIT_MIN_ZOOM);
  });
});

describe("on the page", () => {
  it("the +N more pill and the status strip say the same number; opened, the pill folds that many back", async () => {
    const base = seeded();
    const id = "corp-twin";
    const { api } = seeded({
      twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
      documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "te.yaml": TE, "twin.yaml": twinYaml(id) }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
    renderAt(api, `/twins/${id}/canvas`);
    const more = await screen.findByText(/^\+\d+ more$/);
    const n = Number(/\d+/.exec(more.textContent ?? "")![0]);
    expect(screen.getByTestId("canvas-status").textContent).toContain(`${n} segments behind "+N more" tiles`);
    for (const site of SITES) expect(screen.getAllByTestId("canvas-overflow").some((o) => (o.textContent ?? "").startsWith(`${site} ·`))).toBe(true);
    fireEvent.click(more.closest("[data-testid='canvas-overflow']")!);
    await waitFor(() => expect(screen.getByText(`fold ${n} back`)).toBeTruthy());
  });
});
