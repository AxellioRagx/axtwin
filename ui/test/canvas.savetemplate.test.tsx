/**
 * Save a selection as a template, phase 3 (`docs/design/canvas-add-panel.md` §5.3, §10 "Automated
 * (phase 3)"): from a cast twin the template strips provenance and observed addresses; counts become
 * settings and outgoing links ports; it round-trips (save -> drop) to the same structure with no
 * provenance; the gesture, the dialog, the write and My templates.
 *
 * The fixture is tier-cast's gw-2 branch (a cast twin, `catalog/twins/tier-cast` in the user's
 * checkout), trimmed: gw-2 with its two discovered LANs, a ×3 endpoint group and a web server; the
 * transit to gw and gw itself outside the selection.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Persona } from "../src/api/types";
import { buildTemplate, carriedBindings, cleanName, MY_TEMPLATES_KEY, proposeTemplate, selectionSet, type SaveChoices } from "../src/features/edit-canvas/add/saveTemplate";
import { applyTemplateDrop } from "../src/features/edit-canvas/add/template";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";
import { openPalette, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

const CAST = `twin_config:
  id: tier-cast
  description: "[cast pit-7ea828d3fe-1795136400] Discovered from axquery."
  segments:
  - name: seg-10-74-41-0
    cidr: 10.74.41.0/24
    zone_type: simple
    source: discovered
    confidence: 1.0
    vantage: XPress-SW-B/XPress-SW-A:1
    observation:
      windows_seen: 1
      windows_total: 1
      occupancy: 1.0
  - name: seg-10-74-42-0
    cidr: 10.74.42.0/24
    zone_type: simple
    source: discovered
    confidence: 1.0
    vantage: XPress-SW-B/XPress-SW-A:1
  - name: transit-2
    cidr: 10.254.0.8/29
    zone_type: simple
    source: unconfirmed
  - name: seg-10-74-20-0
    cidr: 10.74.20.0/24
    zone_type: simple
    source: discovered
  nodes:
  - name: generic-endpoint-10-74-41-core
    image: debian-12-cloud@v1
    net: seg-10-74-41-0
    role: generic-endpoint-10-74-41
    count: 3
    observation:
      windows_seen: 1
      role_confidence: 0.3
    source: discovered
    confidence: 1.0
    vantage: XPress-SW-B/XPress-SW-A:1
    install:
    - pkg: curl
  - name: web-tier-10-74-42-core
    image: ubuntu-server-22@v5
    nets:
    - { segment: seg-10-74-42-0, ip: 10.74.42.7 }
    role: web-tier-10-74-42
    listen:
    - port: 80
      proto: tcp
    source: discovered
    confidence: 1.0
    vantage: XPress-SW-B/XPress-SW-A:1
  - name: gw
    role: gw
    kind: router
    image: debian-12-cloud@v1
    nets:
    - segment: seg-10-74-20-0
    - segment: transit-2
    source: discovered
    vantage: XPress-SW-B/XPress-SW-A:1
  - name: gw-2
    role: gw
    kind: router
    image: debian-12-cloud@v1
    nets:
    - segment: seg-10-74-41-0
    - segment: seg-10-74-42-0
    - segment: transit-2
    source: discovered
    vantage: XPress-SW-B/XPress-SW-A:1
  conduits:
  - from: seg-10-74-20-0
    to: seg-10-74-41-0
    source: discovered
    vantage: XPress-SW-B/XPress-SW-A:1
  - from: seg-10-74-41-0
    to: seg-10-74-42-0
    source: discovered
    vantage: XPress-SW-B/XPress-SW-A:1
`;
const TFC = `traffic-profile:
  target: tier-cast
  assignments:
  - role: generic-endpoint-10-74-41
    persona: cast-generic-endpoint-10-74-41
  - role: web-tier-10-74-42
    persona: cast-web-tier-10-74-42
`;
const TAP = `tap-plan:
  target: tier-cast
  bindings:
  - selector: { scope: segment, target: seg-10-74-41-0 }
    sensors: [XPress-SW-A]
    mode: exclusive
`;
const MANIFEST = "twin:\n  id: tier-cast\n  tiers:\n    te:\n      default: te.yaml\n    tfc:\n      default: tfc/default.yaml\n    tap:\n      default: tap/default.yaml\n";
const DOC = { id: "tier-cast", teFile: "te.yaml", files: { "te.yaml": CAST, "twin.yaml": MANIFEST, "tfc/default.yaml": TFC, "tap/default.yaml": TAP } };
const BRANCH = ["gw-2", "generic-endpoint-10-74-41-core", "web-tier-10-74-42-core"];
const PROFILES = [{ name: "linux-server", image: "debian-12-cloud@v1" }];
/** The library has the web server's cast persona but not the endpoints' (a twin-local one). */
const LIB: Persona[] = [{ id: "cast-web-tier-10-74-42", os_family: "linux", drive: "scripted", source: "catalog", requires: [], summary: "", emits: [] }];
const choices = (over: Partial<SaveChoices> = {}): SaveChoices => ({ label: "Tier branch", description: "gw-2's branch", icon: "branch", prefix: "br", names: {}, counts: {}, ...over });
const EMPTY = { id: "t", teFile: "te.yaml", files: { "te.yaml": "twin_config:\n  id: t\n" } };

describe("the proposal (§5.3)", () => {
  it("suggests clean names for discovered ones", () => {
    expect(cleanName("web-tier-10-74-20-1", "node")).toBe("web-tier");
    expect(cleanName("generic-endpoint-10-74-41-core", "node")).toBe("generic-endpoint");
    expect(cleanName("seg-10-74-41-0", "segment")).toBe("lan");
    expect(cleanName("gw-2", "node")).toBe("gw-2");
    expect(cleanName("office-lan", "segment")).toBe("office-lan");
  });

  it("takes the segments every attached node of which is selected; the transit to gw stays outside", () => {
    const m = readDoc(DOC);
    expect(selectionSet(m, { kind: "node", names: BRANCH })).toEqual({ devices: ["gw-2"], segments: ["seg-10-74-41-0", "seg-10-74-42-0"], hosts: ["generic-endpoint-10-74-41-core", "web-tier-10-74-42-core"], leftOut: [] });
    // A host selected without its router: no segment of its own inside, left out.
    expect(selectionSet(m, { kind: "node", names: ["generic-endpoint-10-74-41-core"] }).leftOut).toEqual(["generic-endpoint-10-74-41-core"]);
  });

  it("counts become settings, CIDRs subnet: auto, outgoing links ports; what is stripped is counted", () => {
    const p = proposeTemplate(readDoc(DOC), { kind: "node", names: BRANCH });
    expect(p.names.map((c) => `${c.from}>${c.to}`)).toEqual(["gw-2>gw-2", "generic-endpoint-10-74-41-core>generic-endpoint", "web-tier-10-74-42-core>web-tier", "seg-10-74-41-0>lan", "seg-10-74-42-0>lan-2"]);
    expect(p.counts).toEqual([
      { node: "generic-endpoint-10-74-41-core", param: "generic_endpoint", label: "generic-endpoint: N", value: 3, on: true },
      { node: "web-tier-10-74-42-core", param: "web_tier", label: "web-tier: N", value: 1, on: false },
    ]);
    expect(p.ports).toEqual([{ name: "transit-2", device: "gw-2", reason: "its leg on transit-2" }]);
    expect(p.stripped.addresses).toBe(3); // two CIDRs and the web server's pinned IP
    expect(p.stripped.provenance).toBeGreaterThanOrEqual(12);
    expect(p.stripped.taps).toBe(1);
    expect(p.stripped.conduits).toBe(1); // 20<->41 leaves the selection
    expect(carriedBindings(readDoc(DOC), p.set.hosts, LIB)).toEqual({ carried: [{ node: "web-tier-10-74-42-core", persona: "cast-web-tier-10-74-42", intensity: null, schedule: null }], stripped: ["generic-endpoint-10-74-41-core: cast-generic-endpoint-10-74-41"] });
  });

  it("the template carries no provenance, no observed address and no tap", () => {
    const m = readDoc(DOC);
    const t = buildTemplate(m, proposeTemplate(m, { kind: "node", names: BRANCH }), choices(), LIB, { "host:gw-2": { x: 500, y: 300 }, "host:web-tier-10-74-42-core": { x: 560, y: 420 } }, []);
    const json = JSON.stringify(t);
    for (const word of ["source", "confidence", "vantage", "observation", "discovered", "10.74.", "XPress", "install", "listen", "tap", "ip\""]) expect(json).not.toContain(word);
    expect(t).toMatchObject({ id: "tier-branch", builtin: false, label: "Tier branch", ports: [{ name: "transit-2", device: "{prefix}-gw-2" }], layout: { "{prefix}-gw-2": { x: 0, y: 0 }, "{prefix}-web-tier": { x: 60, y: 120 } } });
    expect(t.params.map((p) => `${p.name}:${String(p.default)}`)).toEqual(["generic_endpoint:3", "prefix:br", "subnet:auto"]);
    expect((t.fragment["nodes"] as Array<Record<string, unknown>>).find((n) => n["name"] === "{prefix}-generic-endpoint")).toEqual({ name: "{prefix}-generic-endpoint", role: "generic-endpoint-10-74-41", image: "debian-12-cloud@v1", count: "{generic_endpoint}", net: "{prefix}-lan" });
    expect(t.fragment["conduits"]).toEqual([{ from: "{prefix}-lan", to: "{prefix}-lan-2" }]);
    expect(t.fragment["assignments"]).toEqual([{ node: "{prefix}-web-tier", persona: "cast-web-tier-10-74-42" }]);
  });

  it("round-trips: saved from the cast twin, dropped on an empty canvas, the same structure with no provenance", () => {
    const m = readDoc(DOC);
    const t = buildTemplate(m, proposeTemplate(m, { kind: "node", names: BRANCH }), choices(), LIB, {}, []);
    const r = applyTemplateDrop(EMPTY, readDoc(EMPTY), t, {}, PROFILES, null);
    const got = readDoc({ ...EMPTY, files: r.files });
    expect(got.findings.filter((f) => f.severity === "error")).toEqual([]);
    // The structure: one router on both LANs, the ×3 group on one, the web server on the other, their conduit.
    const shape = (te: typeof got.te, names: Record<string, string>) => ({
      routers: te.nodes.filter((n) => n.kind === "router").map((n) => n.nets.map((l) => names[l.segment] ?? l.segment).sort()),
      hosts: te.nodes.filter((n) => n.kind === "host").map((n) => `${n.role}×${n.countValue}@${names[n.nets[0]!.segment] ?? n.nets[0]!.segment}:${n.image}`).sort(),
      conduits: te.conduits.map((c) => [names[c.from] ?? c.from, names[c.to] ?? c.to].sort().join("<>")),
    });
    const before = { "seg-10-74-41-0": "A", "seg-10-74-42-0": "B" };
    const after = { "br-lan": "A", "br-lan-2": "B" };
    const orig = readDoc(DOC).te;
    const branchOnly = { ...orig, nodes: orig.nodes.filter((n) => BRANCH.includes(n.name)).map((n) => (n.kind === "router" ? { ...n, nets: n.nets.filter((l) => l.segment !== "transit-2") } : n)), conduits: orig.conduits.filter((c) => c.from !== "seg-10-74-20-0") };
    expect(shape(got.te, after)).toEqual(shape(branchOnly, before));
    // No provenance and no observed address came back.
    expect(r.files["te.yaml"]).not.toMatch(/discovered|vantage|confidence|10\.74\.|observation/);
    expect(got.te.nodes.every((n) => n.nets.every((l) => l.ip === null))).toBe(true);
    expect(r.files["tap/default.yaml"]).toBeUndefined();
    expect(r.ports).toEqual(["br-gw-2"]);
    expect(readTfc(r.files["tfc/default.yaml"]!).assignments.map((a) => `${a.target}:${a.persona}`)).toEqual(["br-web-tier:cast-web-tier-10-74-42"]);
  });
});

describe("the gesture, the dialog and My templates", () => {
  function withCast() {
    const base = seeded();
    return seeded({
      personas: LIB,
      templates: [],
      twins: { ...base.state.twins, "tier-cast": { ...base.state.twins["corp-auth"]!, id: "tier-cast" }, blank: { ...base.state.twins["corp-auth"]!, id: "blank" } },
      documents: {
        ...base.state.documents,
        "tier-cast": { id: "tier-cast", te_file: "te.yaml", files: DOC.files, skipped: [] },
        blank: { id: "blank", te_file: "te.yaml", files: { "te.yaml": "twin_config:\n  id: blank\n", "twin.yaml": twinYaml("blank") }, skipped: [] },
      },
      twinConfigs: [...base.state.twinConfigs, { id: "tier-cast", description: null, cluster: null, parameterized: false, param_names: [], error: null }, { id: "blank", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
  }

  it("select, right-click 'Save as template…', edit, save: it is under My templates at once; dropped on another canvas it lands", async () => {
    const { api, state } = withCast();
    const r = renderAt(api, "/twins/tier-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    const host = (n: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === n)!;
    const router = (n: string) => screen.getAllByTestId("canvas-router").find((h) => h.dataset["nodeId"] === n)!;
    fireEvent.click(router("gw-2"));
    fireEvent.click(host("generic-endpoint-10-74-41-core"), { shiftKey: true });
    fireEvent.click(host("web-tier-10-74-42-core"), { shiftKey: true });
    fireEvent.contextMenu(router("gw-2"));
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: "Save as template…" }));
    const dlg = within(await screen.findByTestId("save-template-dialog"));
    expect(dlg.getByTestId("st-ports").textContent).toContain("transit-2");
    expect(dlg.getByTestId("st-stripped").textContent).toContain("3 observed addresses");
    expect(dlg.getByTestId("st-stripped").textContent).toContain("1 tap (bound to sensors outside the selection)");
    expect(dlg.getByTestId("st-stripped").textContent).toContain("generic-endpoint-10-74-41-core: cast-generic-endpoint-10-74-41");
    fireEvent.change(dlg.getByLabelText("Name"), { target: { value: "Tier branch" } });
    fireEvent.change(dlg.getByLabelText("New name for gw-2"), { target: { value: "edge" } });
    fireEvent.click(dlg.getByTestId("st-save"));
    await waitFor(() => expect(screen.queryByTestId("save-template-dialog")).toBeNull());
    const write = state.writes.find((w) => w.op === "create-template")!;
    expect((write.body as { id: string }).id).toBe("tier-branch");
    expect(JSON.parse(localStorage.getItem(MY_TEMPLATES_KEY)!)).toEqual(["tier-branch"]);
    openPalette();
    await waitFor(() => expect(within(screen.getByTestId("templates-mine")).getAllByTestId("add-template").map((c) => c.dataset["id"])).toEqual(["tier-branch"]));
    expect(screen.queryByTestId("templates-team")).toBeNull();
    // Dropped on an empty canvas.
    r.unmount();
    renderAt(api, "/twins/blank/canvas");
    await screen.findByTestId("canvas");
    openPalette();
    await waitFor(() => expect(screen.getAllByTestId("add-template").length).toBe(1));
    fireEvent.click(screen.getAllByTestId("add-template")[0]!);
    fireEvent.click(screen.getByTestId("template-add"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"]).sort()).toEqual(["tpl-generic-endpoint", "tpl-web-tier"]));
    expect(screen.getAllByTestId("canvas-router").map((h) => h.dataset["nodeId"])).toEqual(["tpl-edge"]);
  });

  it("a refusal on save shows the server's reason and keeps the dialog", async () => {
    const { api } = withCast();
    api.createTemplate = async () => {
      throw new (await import("../src/api/client")).ApiError({ kind: "http", status: 422, message: "template 'x' fails validation", detail: { code: "invalid", findings: [{ message: "unknown image" }] }, url: "" });
    };
    renderAt(api, "/twins/tier-cast/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-router").find((h) => h.dataset["nodeId"] === "gw-2")!);
    fireEvent.contextMenu(window.document.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 200 });
    fireEvent.click(within(await screen.findByTestId("context-menu")).getByRole("button", { name: "Save as template…" }));
    fireEvent.click(await screen.findByTestId("st-save"));
    expect((await screen.findByTestId("st-error")).textContent).toContain("unknown image");
    expect(screen.getByTestId("save-template-dialog")).toBeTruthy();
  });
});
