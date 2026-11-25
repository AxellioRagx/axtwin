/**
 * TRex app mixes are not assignable to a VM (UI review 2a, 2026-11-24;
 * `engine/src/axrange/trafficgen/SPEC.md`, validator TGN-060). A
 * `drive: trex-astf` persona with `mix:` plays from emulated instances on a
 * traffic line, so the group, member and bulk persona dropdowns and the Add
 * panel's role picker hide it until the canvas has the instances split. The
 * per-VM `knobs.side` form (trex-test's crowd) has no mix and stays.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Persona } from "../src/api/types";
import { assignablePersonas, isAppMix, rolesFor } from "../src/features/edit-canvas/add/catalog";
import { PERSONAS, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const MIX: Persona = {
  id: "web-browse", os_family: "linux", drive: "trex-astf", source: "catalog", requires: [], summary: "browse",
  emits: [], category: "traffic", label: "Web browsing (TRex)", blurb: "",
  mix: [{ app: "http-browse", weight: 7 }, { app: "tls-session", weight: 3 }], real_server_ok: false,
};
const SIDE: Persona = {
  id: "trex-crowd-server", os_family: "linux", drive: "trex-astf", source: "twin", requires: [], summary: "server side",
  emits: [], category: "traffic", label: "TRex server side", blurb: "",
};
const ALL = [...PERSONAS, MIX, SIDE];

const TE = `twin_config:
  id: mixy
  segments:
    - { name: lan, cidr: 10.72.0.0/24 }
  nodes:
    - name: web
      role: web-tier
      image: debian-12-cloud@v1
      count: 2
      net: lan
`;
const TFC = `traffic-profile:
  target: mixy
  assignments:
    - node: web
      persona: web-tier
`;

function withTwin() {
  const base = seeded();
  const id = "mixy";
  return seeded({
    personas: ALL,
    twins: { ...base.state.twins, [id]: { ...base.state.twins["corp-auth"]!, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "twin.yaml": twinYaml(id), "te.yaml": TE, "tfc/default.yaml": TFC }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}
const options = (sel: HTMLElement) => Array.from((sel as HTMLSelectElement).options).map((o) => o.value);
const hostIcon = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("TRex app mixes are not assignable to a VM", () => {
  it("isAppMix is a trex-astf persona with a mix; the knobs.side form is not one", () => {
    expect(isAppMix(MIX)).toBe(true);
    expect(isAppMix(SIDE)).toBe(false);
    expect(isAppMix({ drive: "scripted", mix: [{ app: "x", weight: 1 }] })).toBe(false);
    expect(assignablePersonas(ALL).map((p) => p.id)).not.toContain("web-browse");
    expect(assignablePersonas(ALL).map((p) => p.id)).toContain("trex-crowd-server");
  });

  it("the Add panel's role picker (Advanced: traffic) leaves the mixes out", () => {
    const ids = rolesFor("server", "linux", ALL, true).flatMap((g) => g.personas.map((p) => p.id));
    expect(ids).toContain("trex-crowd-server");
    expect(ids).not.toContain("web-browse");
  });

  it("the group, member and bulk persona dropdowns do not offer a mix", async () => {
    const { api } = withTwin();
    renderAt(api, "/twins/mixy/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(hostIcon("web"));
    const panel = await screen.findByTestId("node-panel");
    const group = within(panel).getByLabelText("Persona");
    expect(options(group)).toContain("web-tier");
    expect(options(group)).toContain("trex-crowd-server");
    expect(options(group)).not.toContain("web-browse");
    for (const sel of within(panel).queryAllByLabelText(/^Persona (of|for picked)/)) {
      expect(options(sel)).not.toContain("web-browse");
    }
    fireEvent.doubleClick(hostIcon("web"));
    const chips = within(await screen.findByTestId("member-chips")).getAllByTestId("member-chip");
    fireEvent.click(chips[0]!);
    const mp = await screen.findByTestId("member-panel");
    await waitFor(() => expect(options(within(mp).getByLabelText("Member persona"))).toContain("web-tier"));
    expect(options(within(mp).getByLabelText("Member persona"))).not.toContain("web-browse");
  });
});
