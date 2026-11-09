/**
 * Tap chip drag-to-move (topology-layers §8; carried along by docs/design/targets-and-lab.md §5): a chip dropped on a
 * segment rewrites its binding to `{scope: segment, target: <segment>}` through the working-copy path.
 *
 * jsdom measures every element 0, so React Flow's drag cannot be driven here. The drag handler's decision is the
 * pure `tapDropSelector` (tested on laid-out nodes below), and what it decides goes through `onRetargetTap` ->
 * `retargetTap` -> the editor -> the deploy's working copy, the same callback the tap inspector's Scope/Target use
 * (driven below on a deployed twin, asserting the PUT of the working copy).
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { tapDropSelector } from "../src/features/edit-canvas/Canvas";
import { readDoc, retargetTap } from "../src/features/edit-canvas/edit/document";
import { readTap } from "../src/features/edit-canvas/edit/tap";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { DEPLOY, seeded, settle, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TE = `twin_config:
  id: drag
  segments:
    - { name: office, cidr: 10.71.10.0/24 }
    - { name: servers, cidr: 10.71.20.0/24 }
  nodes:
    - name: ws
      role: corp-workstation
      image: debian-12-cloud@v1
      net: office
    - name: file-server
      role: file-server
      image: ubuntu-server-22@v5
      net: servers
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
`;
const TAP = `tap-plan:\n  target: drag\n  bindings:\n    - selector: { scope: boundary, target: [office, servers] }\n      sensors: [XPress-SW-A]\n      mode: exclusive\n`;

// Laid-out nodes as React Flow holds them at drag stop: two segment bodies, the router between them.
const seg = (name: string, x: number) => ({ id: `seg:${name}`, position: { x, y: 0 }, width: 300, height: 200, data: { kind: "segment", name } }) as never;
const router = { id: "dev:gw", position: { x: 320, y: 60 }, width: 60, height: 40, data: { kind: "router", name: "gw" } } as never;
const NODES = [seg("office", 0), seg("servers", 400), router];

describe("dropping a tap chip", () => {
  const te = readTe(TE);
  const noEdge = () => undefined;

  it("on a segment's body: {scope: segment, target: <segment>}", () => {
    expect(tapDropSelector(NODES, { x: 500, y: 80 }, te, noEdge)).toEqual({ scope: "segment", target: "servers" });
    expect(tapDropSelector(NODES, { x: 100, y: 80 }, te, noEdge)).toEqual({ scope: "segment", target: "office" });
  });

  it("on the router joining two segments: their boundary; on a segment's link: that segment; elsewhere: nothing", () => {
    expect(tapDropSelector(NODES, { x: 340, y: 70 }, te, noEdge)).toEqual({ scope: "boundary", target: ["office", "servers"] });
    expect(tapDropSelector(NODES, { x: 1000, y: 900 }, te, () => ({ kind: "seglink", segment: "office" }))).toEqual({ scope: "segment", target: "office" });
    expect(tapDropSelector(NODES, { x: 1000, y: 900 }, te, noEdge)).toBeNull();
  });

  it("the selector it decides rewrites only that binding's selector", () => {
    const doc = { id: "drag", teFile: "te.yaml", files: { "te.yaml": TE, "twin.yaml": twinYaml("drag").replace("tfc:", "tap:\n      default: tap/default.yaml\n    tfc:"), "tap/default.yaml": TAP } };
    const sel = tapDropSelector(NODES, { x: 500, y: 80 }, te, noEdge)!;
    const files = retargetTap(doc, readDoc(doc), 0, sel);
    expect(files["tap/default.yaml"]).toBe(TAP.replace("{ scope: boundary, target: [office, servers] }", "{ scope: segment, target: servers }"));
  });

  it("on a deployed twin the move lands in the working copy (PUT /api/deploys/{id}/config), one taps change", async () => {
    const deploy = { ...DEPLOY, deploy_id: "dep-drag", scenario_id: "drag" };
    const base = seeded();
    const { api, state } = seeded({
      twins: { ...base.state.twins, drag: { ...base.state.twins["corp-auth"]!, id: "drag" } },
      documents: { ...base.state.documents, drag: { id: "drag", te_file: "te.yaml", files: { "te.yaml": TE, "twin.yaml": twinYaml("drag").replace("tfc:", "tap:\n      default: tap/default.yaml\n    tfc:"), "tap/default.yaml": TAP }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "drag", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
      deploys: [deploy],
    });
    renderAt(api, "/twins/drag/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-tap")[0]!);
    const panel = await screen.findByTestId("tap-panel");
    // The same onRetargetTap the drop calls: boundary -> segment.
    fireEvent.change(within(panel).getByLabelText("Tap scope"), { target: { value: "segment" } });
    await settle();
    const puts = state.writes.filter((w) => w.op === "put-deploy-config" && w.id === "dep-drag") as Array<{ body: { files: Record<string, string> } }>;
    expect(puts.length).toBeGreaterThan(0);
    const tap = puts.at(-1)!.body.files["tap/default.yaml"]!;
    expect(readTap(tap).bindings[0]).toMatchObject({ scope: "segment", sensors: ["XPress-SW-A"] });
    expect(tap).toMatch(/selector: \{ scope: segment, target: (office|servers) \}/);
    expect(state.workingCopies["dep-drag"]!.files["tap/default.yaml"]).toBe(tap);
  });
});
