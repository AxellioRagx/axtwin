/**
 * Conduits on the design canvas and Check findings pinned to them (`SPEC.md` "Conduits"): every
 * `conduits[]` entry is drawn as a dotted "conduit" connector between its segments, in the warning
 * colour when no node implements it; selecting one offers Remove and the router-leg fix; the engine's
 * finding (with its `subject`) pins on the connector. tier-cast's shape: a conduit between two
 * segments that share no router.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Position, ReactFlowProvider } from "@xyflow/react";
import { afterEach, describe, expect, it } from "vitest";

import { placeTree } from "../src/features/edit-canvas/canvas/tree";
import { findingTarget, targetKey, type FindingPin } from "../src/features/edit-canvas/edit/check";
import { documentGraph, readDoc } from "../src/features/edit-canvas/edit/document";
import { EditContext } from "../src/features/edit-canvas/edit/EditContext";
import { buildGraph } from "../src/features/edit-canvas/model/graph";
import { ArcEdge, FindingsContext } from "../src/features/edit-canvas/nodes";
import { addConduit, readTe, removeConduit, renameSegment } from "../src/features/edit-canvas/edit/te";
import { check, checkStatus, seeded, segment, tab, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => localStorage.clear());

const TE = `twin_config:
  id: cond
  segments:
  - name: seg-10-74-20-0
    cidr: 10.74.20.0/24
  - name: seg-10-74-31-0
    cidr: 10.74.31.0/24
  - name: seg-10-74-41-0
    cidr: 10.74.41.0/24
  nodes:
  - name: gw
    kind: router
    image: ubuntu-server-22@v5
    nets:
      - { segment: seg-10-74-20-0 }
      - { segment: seg-10-74-41-0 }
  - name: r31
    kind: router
    image: ubuntu-server-22@v5
    net: seg-10-74-31-0
  - name: ws01
    image: ubuntu-server-22@v5
    net: seg-10-74-20-0
  conduits:
  - from: seg-10-74-20-0
    to: seg-10-74-31-0
    source: discovered
    vantage: XPress-SW-B/XPress-SW-A:1
  - from: seg-10-74-20-0
    to: seg-10-74-41-0
    source: discovered
`;

function withCond() {
  const base = seeded();
  return seeded({
    twins: { ...base.state.twins, cond: { ...base.state.twins["corp-auth"]!, id: "cond" } },
    documents: { ...base.state.documents, cond: { id: "cond", te_file: "te.yaml", files: { "te.yaml": TE, "twin.yaml": twinYaml("cond") }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id: "cond", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}


describe("the conduit model", () => {
  it("reads conduits, adds and removes one as a line splice, and a segment rename follows into them", () => {
    const te = readTe(TE);
    expect(te.conduits.map((c) => [c.from, c.to, c.source])).toEqual([["seg-10-74-20-0", "seg-10-74-31-0", "discovered"], ["seg-10-74-20-0", "seg-10-74-41-0", "discovered"]]);
    const added = addConduit(TE, "seg-10-74-31-0", "seg-10-74-41-0");
    expect(readTe(added).conduits.at(-1)).toMatchObject({ from: "seg-10-74-31-0", to: "seg-10-74-41-0", source: "authored" });
    for (const line of TE.split("\n")) expect(added.split("\n")).toContain(line);
    const removed = removeConduit(TE, 0);
    expect(readTe(removed).conduits.map((c) => c.to)).toEqual(["seg-10-74-41-0"]);
    expect(readTe(removeConduit(removed, 0)).conduits).toEqual([]);
    const renamed = renameSegment(TE, "seg-10-74-31-0", "core");
    expect(readTe(renamed).conduits[0]).toMatchObject({ from: "seg-10-74-20-0", to: "core" });
  });

  it("maps a finding's subject (or its conduit location) to the conduit", () => {
    const m = readDoc({ id: "cond", teFile: "te.yaml", files: { "te.yaml": TE } });
    const bySubject = findingTarget({ rule_id: "TC-CONDUIT", severity: "error", message: "x", location: null, subject: { kind: "conduit", from: "seg-10-74-31-0", to: "seg-10-74-20-0" } }, m)!;
    expect(targetKey(bySubject)).toBe("conduit:seg-10-74-20-0|seg-10-74-31-0");
    expect(findingTarget({ rule_id: "TC-CONDUIT", severity: "error", message: "x", location: "twin_config.conduits[seg-10-74-20-0->seg-10-74-31-0]" }, m)).toEqual({ kind: "conduit", from: "seg-10-74-20-0", to: "seg-10-74-31-0" });
    expect(findingTarget({ rule_id: "TC-NODE-SEGMENT", severity: "error", message: "x", location: null, subject: { kind: "node", name: "ws01" } }, m)).toEqual({ kind: "node", name: "ws01" });
  });
});

/** The files on screen: a Check sends exactly them. */
const lastTe = (state: ReturnType<typeof seeded>["state"]) => readTe((state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files["te.yaml"]!);

/** The conduit arc as the canvas draws it (jsdom measures no node, so React Flow draws no edge in a page test). */
// jsdom has no SVG layout: the edge label measures itself with getBBox.
if (!(SVGElement.prototype as unknown as { getBBox?: unknown }).getBBox) (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () => ({ x: 0, y: 0, width: 40, height: 12 }) as DOMRect;

function drawArc(te: string, conduitsOn: Array<{ a: string; b: string }>, pins: Map<string, FindingPin> = new Map()) {
  const model = readDoc({ id: "cond", teFile: "te.yaml", files: { "te.yaml": te } });
  const edit = { model, locked: false, attachArmed: false, attachFrom: null, placing: null, rehomeTarget: null, onContextMenu: () => {}, membersOpen: new Set<string>(), onToggleMembers: () => {}, onSelectMember: () => {}, onMoveMember: () => {} };
  const data = { kind: "arc" as const, key: "k", arcKind: "asserted" as const, bytes: 0, flows: 0, pairs: conduitsOn.length, label: "x", conduits: conduitsOn };
  const props = { id: "arc:asserted:k", source: "a", target: "b", sourceX: 0, sourceY: 0, targetX: 200, targetY: 0, sourcePosition: Position.Bottom, targetPosition: Position.Bottom, data, selected: false } as unknown as Parameters<typeof ArcEdge>[0];
  return render(
    <ReactFlowProvider>
      <FindingsContext.Provider value={pins}>
        <EditContext.Provider value={edit}>
          <svg>
            <ArcEdge {...props} />
          </svg>
        </EditContext.Provider>
      </FindingsContext.Provider>
    </ReactFlowProvider>,
  );
}

describe("conduits on the canvas", () => {
  it("the design placement carries every conduit as an arc between its segments' switches", () => {
    const m = readDoc({ id: "cond", teFile: "te.yaml", files: { "te.yaml": TE } });
    const g = documentGraph(m, "cond");
    const p = placeTree(buildGraph(g.exportData, g.cast), {}, { expanded: [], extraLegs: g.extraLegs, uplinks: g.uplinks, tree: g.tree });
    const arcs = p.edges.filter((e) => e.type === "arc");
    expect(arcs.map((e) => [e.source, e.target, (e.data as { conduits?: unknown }).conduits])).toEqual([
      ["vsw:seg-10-74-20-0", "vsw:seg-10-74-31-0", [{ a: "seg-10-74-20-0", b: "seg-10-74-31-0" }]],
      ["vsw:seg-10-74-20-0", "vsw:seg-10-74-41-0", [{ a: "seg-10-74-20-0", b: "seg-10-74-41-0" }]],
    ]);
  });

  it("an unimplemented conduit draws dotted, labelled and in the warning colour, its finding pinned; an implemented one plain", () => {
    const pins = new Map<string, FindingPin>([["conduit:seg-10-74-20-0|seg-10-74-31-0", { severity: "error", messages: ["TC-CONDUIT: no node on both"] }]]);
    const open = drawArc(TE, [{ a: "seg-10-74-20-0", b: "seg-10-74-31-0" }], pins);
    const g = open.getByTestId("canvas-conduit");
    expect(g.dataset["open"]).toBe("1");
    expect(g.classList.contains("open")).toBe(true);
    // Labels collapse: a failing conduit keeps a short "no route" until hovered or selected.
    expect(g.dataset["label"]).toBe("no route");
    fireEvent.mouseEnter(g);
    expect(g.dataset["label"]).toBe("conduit · no route");
    expect(open.getByTestId("finding-badge").dataset["severity"]).toBe("error");
    open.unmount();
    const ok = drawArc(TE, [{ a: "seg-10-74-20-0", b: "seg-10-74-41-0" }]);
    expect(ok.getByTestId("canvas-conduit").dataset["open"]).toBeUndefined();
    expect(ok.getByTestId("canvas-conduit").dataset["label"]).toBe(""); // no label until hovered or selected
    expect(ok.queryByTestId("finding-badge")).toBeNull();
  });

  it("Check returns the conduit finding (not a failed request); it selects the conduit, whose panel removes it; the next Check passes", async () => {
    const { api, state } = withCond();
    renderAt(api, "/twins/cond/canvas");
    await screen.findAllByTestId("canvas-segment");
    await check("invalid");
    tab("Checks");
    expect(screen.queryByText(/the API could not validate/)).toBeNull();
    const f = screen.getAllByTestId("finding").find((x) => x.dataset["rule"] === "TC-CONDUIT")!;
    expect(f.dataset["target"]).toBe("conduit:seg-10-74-20-0|seg-10-74-31-0");
    fireEvent.click(within(f).getByRole("button"));
    tab("Selection");
    const panel = await screen.findByTestId("conduit-panel");
    expect(within(panel).getByTestId("conduit-state").textContent).toContain("no node has a leg on both segments");
    expect(within(panel).getByTestId("conduit-source").textContent).toBe("discovered");
    expect((within(panel).getByLabelText("Router to fix the conduit") as HTMLSelectElement).options[0]!.textContent).toContain("gw (+ leg on seg-10-74-31-0)");
    fireEvent.click(within(panel).getByTestId("conduit-remove"));
    await waitFor(() => expect(screen.queryByTestId("conduit-panel")).toBeNull());
    await check("valid");
    expect(lastTe(state).conduits.map((c) => `${c.from}>${c.to}`)).toEqual(["seg-10-74-20-0>seg-10-74-41-0"]);
    expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: remove conduit seg-10-74-20-0 ↔ seg-10-74-31-0");
  });

  it("Add a router leg to fix: the router on one segment gets a leg on the other, and the conduit is implemented", async () => {
    const { api, state } = withCond();
    renderAt(api, "/twins/cond/canvas");
    await screen.findAllByTestId("canvas-segment");
    await check("invalid");
    tab("Checks");
    fireEvent.click(within(screen.getAllByTestId("finding").find((x) => x.dataset["rule"] === "TC-CONDUIT")!).getByRole("button"));
    tab("Selection");
    const panel = await screen.findByTestId("conduit-panel");
    fireEvent.click(within(panel).getByRole("button", { name: "Add a router leg to fix" }));
    await waitFor(() => expect(within(screen.getByTestId("conduit-panel")).getByTestId("conduit-state").textContent).toContain("implemented"));
    await check("valid");
    expect(lastTe(state).nodes.find((n) => n.name === "gw")!.nets.map((l) => l.segment)).toEqual(["seg-10-74-20-0", "seg-10-74-41-0", "seg-10-74-31-0"]);
  });

  it("right-click a segment, Declare conduit to…, click the other segment: one conduit, one undo step", async () => {
    const { api, state } = withCond();
    renderAt(api, "/twins/cond/canvas");
    await screen.findAllByTestId("canvas-segment");
    fireEvent.contextMenu(segment("seg-10-74-31-0"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByText(/Declare conduit to/));
    expect(screen.getByTestId("conduit-hint").textContent).toContain("conduit from seg-10-74-31-0");
    fireEvent.click(segment("seg-10-74-41-0"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Undo" }).getAttribute("title")).toBe("undo: declare conduit seg-10-74-31-0 ↔ seg-10-74-41-0"));
    expect(screen.queryByTestId("conduit-hint")).toBeNull();
    expect(checkStatus()).toBe("unchecked");
    await check("invalid");
    expect(lastTe(state).conduits.at(-1)).toMatchObject({ from: "seg-10-74-31-0", to: "seg-10-74-41-0", source: "authored" });
  });
});
