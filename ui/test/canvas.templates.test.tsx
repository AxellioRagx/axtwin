/**
 * Canvas templates, phase 2 (`docs/design/canvas-add-panel.md` §5, §10 "Automated (phase 2)"): every
 * built-in template (the shipped `catalog/templates/` files) expands with its default settings and
 * drops into a design the canvas's validator passes; a drop is one undo step; names carry the prefix
 * and CIDRs never collide; the panel's Templates section, its previews and counts, the settings
 * form at the drop point.
 */
import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Template } from "../src/api/types";
import { searchAdd } from "../src/features/edit-canvas/add/catalog";
import { applyTemplateDrop, countsText, evaluate, expandTemplate, settingsValues, substitute, templateCounts } from "../src/features/edit-canvas/add/template";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";
import { parseYaml, toPlain } from "../src/features/edit-canvas/edit/yaml";
import { check, hosts, openPalette, seeded, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

const DIR = resolve(__dirname, "../../catalog/templates");
/** The shipped built-in templates, read as the API serves them (the `template:` mapping). */
const BUILTINS: Template[] = readdirSync(DIR)
  .filter((d) => !d.startsWith("."))
  .map((d) => {
    const t = (toPlain(parseYaml(readFileSync(resolve(DIR, d, "template.yaml"), "utf8")).root) as unknown as { template: Partial<Template> & Pick<Template, "id" | "label"> }).template;
    return { builtin: false, description: "", icon: "", params: [], ports: [], fragment: {}, layout: {}, ...t };
  });
const PROFILES = [
  { name: "linux-server", image: "debian-12-cloud@v1" },
  { name: "linux-client", image: "debian-12-cloud@v1" },
  { name: "windows-server", image: "windows-server-2022@v1" },
  { name: "windows-client", image: "win11-finance@v4" },
  { name: "router", image: "debian-12-cloud@v1" },
];
const EMPTY = { id: "t", teFile: "te.yaml", files: { "te.yaml": "twin_config:\n  id: t\n" } };
const tpl = (id: string) => BUILTINS.find((t) => t.id === id)!;

describe("built-in templates (§5.1)", () => {
  it("ships the eight of §5.1, each built in, with settings and ports", () => {
    expect(BUILTINS.map((t) => t.id).sort()).toEqual(["ad-domain", "branch-office", "datacenter-rack", "dmz", "internet-edge", "office-segment", "ot-cell", "three-tier-web"]);
    for (const t of BUILTINS) {
      expect(t.builtin).toBe(true);
      expect(t.params.length).toBeGreaterThan(0);
      expect(t.ports.length).toBeGreaterThan(0);
    }
  });

  it.each(BUILTINS.map((t) => [t.id]))("%s expands with its defaults into a design the canvas's validator passes", (id) => {
    const t = tpl(id);
    const r = applyTemplateDrop(EMPTY, readDoc(EMPTY), t, {}, PROFILES, { x: 100, y: 50 });
    const m = readDoc({ ...EMPTY, files: r.files });
    expect(m.te.problems).toEqual([]);
    expect(m.findings.filter((f) => f.severity === "error")).toEqual([]);
    // Names carry the prefix; every port is a device of the design; every image resolved.
    const prefix = String(settingsValues(t.params).prefix);
    for (const n of m.te.nodes) expect(n.name.startsWith(`${prefix}-`)).toBe(true);
    for (const p of r.ports) expect(m.te.nodes.some((n) => n.name === p && n.kind !== "host")).toBe(true);
    for (const n of m.te.nodes) if (n.kind !== "switch") expect(n.image).not.toBe("");
    // The layout lands relative to the drop point.
    expect(Object.values(r.positions)).toContainEqual({ x: 100, y: 50 });
  });

  it.each(BUILTINS.map((t) => [t.id]))("%s dropped twice: the second copy is renamed and its CIDRs do not collide", (id) => {
    const t = tpl(id);
    const once = applyTemplateDrop(EMPTY, readDoc(EMPTY), t, {}, PROFILES, null);
    const d1 = { ...EMPTY, files: once.files };
    const twice = applyTemplateDrop(d1, readDoc(d1), t, {}, PROFILES, null);
    const m = readDoc({ ...EMPTY, files: twice.files });
    const cidrs = m.te.segments.map((s) => s.cidr);
    expect(new Set(cidrs).size).toBe(cidrs.length);
    const names = m.te.nodes.map((n) => n.name);
    expect(new Set(names).size).toBe(names.length);
    expect(Object.values(twice.names).every((n) => /-2$/.test(n))).toBe(true);
    expect(m.findings.filter((f) => f.severity === "error")).toEqual([]);
  });

  it("settings drive counts, names and the subnet; a count of 0 drops its group and binding", () => {
    const t = tpl("office-segment");
    const x = expandTemplate(t, { workstations: 21, windows_share: 80, prefix: "hq" }, PROFILES);
    expect(countsText(templateCounts(x))).toBe("1 switch · 22 hosts");
    expect(x.nodes.find((n) => n["name"] === "hq-ws")!["count"]).toBe(17);
    const r = applyTemplateDrop(EMPTY, readDoc(EMPTY), t, { workstations: 5, windows_share: 100, prefix: "hq", subnet: "10.40.7.0/24" }, PROFILES, null);
    const m = readDoc({ ...EMPTY, files: r.files });
    expect(m.te.segments).toMatchObject([{ name: "hq-lan", cidr: "10.40.7.0/24" }]);
    expect(m.te.nodes.map((n) => n.name)).toEqual(["hq-sw", "hq-ws", "hq-printer"]);
    expect(readTfc(r.files["tfc/default.yaml"]!).assignments.map((a) => `${a.target}:${a.persona}`)).toEqual(["hq-ws:corp-workstation"]);
    // A subnet already in the design is refused with the reason.
    const d = { ...EMPTY, files: r.files };
    expect(() => applyTemplateDrop(d, readDoc(d), t, { subnet: "10.40.7.0/25" }, PROFILES, null)).toThrow(/overlaps 10.40.7.0\/24/);
    // `auto` follows the design's own site.
    const r2 = applyTemplateDrop(d, readDoc(d), tpl("three-tier-web"), {}, PROFILES, null);
    expect(readDoc({ ...EMPTY, files: r2.files }).te.segments.slice(1).map((s) => s.cidr)).toEqual(["10.40.8.0/24", "10.40.9.0/24", "10.40.10.0/24"]);
  });

  it("the expression language matches the engine's (templates.py)", () => {
    expect(evaluate("round(21 * 80 / 100)", {})).toBe(17);
    expect(evaluate("max(a, 2) - min(1, 3)", { a: 5 })).toBe(4);
    expect(substitute("{p}-sw-{n + 1}", { p: "x", n: 1 })).toBe("x-sw-2");
    expect(() => evaluate("nope + 1", {})).toThrow();
    expect(() => evaluate("1 / 0", {})).toThrow();
    expect(settingsValues([{ name: "w", type: "percent", default: 80 }], { w: 250 })).toEqual({ w: 100 });
  });
});

describe("the Templates section and a template drop (§5.2)", () => {
  function withTemplates() {
    const base = seeded();
    return seeded({
      templates: BUILTINS,
      twins: { ...base.state.twins, blank: { ...base.state.twins["corp-auth"]!, id: "blank" } },
      documents: { ...base.state.documents, blank: { id: "blank", te_file: "te.yaml", files: { "te.yaml": "twin_config:\n  id: blank\n", "twin.yaml": twinYaml("blank") }, skipped: [] } },
      twinConfigs: [...base.state.twinConfigs, { id: "blank", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    });
  }

  it("lists each built-in with a preview and its device counts; search finds a template", async () => {
    const { api } = withTemplates();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    openPalette();
    const section = within(screen.getByTestId("add-templates"));
    const cards = section.getAllByTestId("add-template");
    expect(cards.map((c) => c.dataset["id"]).sort()).toEqual(BUILTINS.map((t) => t.id).sort());
    expect(section.getAllByTestId("template-preview").length).toBe(BUILTINS.length);
    expect(cards.find((c) => c.dataset["id"] === "office-segment")!.textContent).toContain("1 switch · 21 hosts");
    expect(cards.find((c) => c.dataset["id"] === "three-tier-web")!.textContent).toContain("1 router · 5 hosts");
    expect(searchAdd("branch", [], BUILTINS)[0]).toMatchObject({ kind: "template", template: "branch-office" });
  });

  it("a click opens the settings form; Add lands the template as one undo step with its ports selected", async () => {
    const { api, state } = withTemplates();
    renderAt(api, "/twins/blank/canvas");
    await screen.findByTestId("canvas");
    openPalette();
    fireEvent.click(within(screen.getByTestId("add-templates")).getAllByTestId("add-template").find((c) => c.dataset["id"] === "ad-domain")!);
    const form = within(screen.getByTestId("template-popover"));
    fireEvent.change(form.getByLabelText("Workstations"), { target: { value: "6" } });
    fireEvent.change(form.getByLabelText("Name prefix"), { target: { value: "acme" } });
    expect(form.getByTestId("template-counts").textContent).toBe("1 switch · 8 hosts");
    fireEvent.click(form.getByTestId("template-add"));
    await waitFor(() => expect(hosts()).toEqual(["acme-dc", "acme-fs", "acme-ws"]));
    // acme-lan is carried by the real switch acme-sw (no implied switch is drawn for it).
    expect(screen.queryAllByTestId("canvas-segment")).toEqual([]);
    // The open port, the switch, is the selection.
    await waitFor(() => expect(screen.getAllByTestId("canvas-device").find((d) => d.dataset["nodeId"] === "acme-sw")!.closest(".selected")).not.toBeNull());
    await check();
    const files = (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
    expect(readTfc(files["tfc/default.yaml"]!).assignments.map((a) => a.target)).toEqual(["acme-dc", "acme-fs", "acme-ws"]);
    // One undo: everything back.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryAllByTestId("canvas-host")).toEqual([]));
    expect(screen.queryAllByTestId("canvas-device")).toEqual([]);
  });

  it("Esc drops it with the defaults; Cancel drops nothing", async () => {
    const { api } = withTemplates();
    renderAt(api, "/twins/blank/canvas");
    await screen.findByTestId("canvas");
    openPalette();
    const card = () => within(screen.getByTestId("add-templates")).getAllByTestId("add-template").find((c) => c.dataset["id"] === "dmz")!;
    fireEvent.click(card());
    fireEvent.click(screen.getByTestId("template-cancel"));
    expect(screen.queryAllByTestId("canvas-host")).toEqual([]);
    fireEvent.click(card());
    fireEvent.keyDown(window.document, { key: "Escape" });
    await waitFor(() => expect(hosts()).toEqual(["dmz-proxy", "dmz-web"]));
  });
});
