/**
 * Placement of dropped nodes in the free layout (`SPEC.md` "Free layout", 2026-11-20 round 2):
 * everything a drop or a template makes -- a new segment's implied switch, a host group, the hosts a
 * template's layout does not name -- stands near the drop point, never at the canvas's top-left
 * corner (`add/placeDrop.ts`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { BUS_ICON } from "../src/features/edit-canvas/canvas/bus";
import { DEVICE_H, DEVICE_W } from "../src/features/edit-canvas/layout/geometry";
import { afterEach, describe, expect, it } from "vitest";

import type { Persona, Template } from "../src/api/types";
import { carrierOf, placeDropped } from "../src/features/edit-canvas/add/placeDrop";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { parseYaml, toPlain } from "../src/features/edit-canvas/edit/yaml";
import { seeded } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
/** The untitled design opens at the identity viewport (jsdom lays nothing out, so a fit would be NaN): a client point is a flow point. */
const identityView = () => sessionStorage.setItem("axr-canvas-viewport:twin:__untitled__", JSON.stringify({ x: 0, y: 0, zoom: 1 }));

const persona = (id: string, os: "linux" | "windows", category: string, label: string, requires: string[] = []): Persona => ({ id, os_family: os, drive: "scripted", source: "catalog", requires, summary: "", emits: [], category, label, blurb: "" });
const LIB: Persona[] = [persona("file-server", "linux", "business", "File server", ["corp-workstation"]), persona("corp-workstation", "windows", "endpoint", "Office user")];
const PROFILES = [
  { name: "linux-server", image: "debian-12-cloud@v1" },
  { name: "linux-client", image: "debian-12-cloud@v1" },
  { name: "windows-server", image: "windows-server-2022@v1" },
  { name: "windows-client", image: "win11-finance@v4" },
  { name: "router", image: "debian-12-cloud@v1" },
];
const BRANCH: Template = (() => {
  const t = (toPlain(parseYaml(readFileSync(resolve(__dirname, "../../catalog/templates/branch-office/template.yaml"), "utf8")).root) as unknown as { template: Template }).template;
  return { ...{ description: "", icon: "", params: [], ports: [], fragment: {}, layout: {} }, ...t };
})();

/** A drop at a client point, through the double-click quick-add: a pick lands where it was asked
 *  for (jsdom has no DragEvent, so an HTML5 drop would carry no coordinates). */
function quickAdd(search: string, x: number, y: number, hit = 0) {
  fireEvent.doubleClick(window.document.querySelector(".react-flow__pane")!, { clientX: x, clientY: y });
  const quick = within(screen.getByTestId("quick-add"));
  fireEvent.change(quick.getByLabelText("Quick add search"), { target: { value: search } });
  fireEvent.click(quick.getAllByTestId("add-hit")[hit]!);
}
/** Every drawn node's absolute top-left, by its pin-style key (`data-key`). */
function positions(): Record<string, { x: number; y: number }> {
  const out: Record<string, { x: number; y: number }> = {};
  for (const el of window.document.querySelectorAll<HTMLElement>("[data-key][data-x]")) out[el.dataset["key"]!] = { x: Number(el.dataset["x"]), y: Number(el.dataset["y"]) };
  return out;
}
const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
/** The template's own laid-out box: its layout points plus one device box (the branch's gw at 0,0 and sw at 0,140). */
const TEMPLATE_BOX = 140 + 66;

describe("a drop in the free layout lands together, at the drop point", () => {
  it("a File server with 'Add 5 workstations' on the empty canvas: its segment's switch just above it, the workstations in its row, all near the drop", async () => {
    const { api } = seeded({ personas: LIB, imageProfiles: PROFILES });
    identityView();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());
    expect(screen.getByTestId("layout-mode").dataset["mode"]).toBe("free");
    quickAdd("file", 600, 400);
    fireEvent.click(await screen.findByTestId("drop-needs-add"));
    fireEvent.click(screen.getByTestId("drop-add"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").length).toBe(2));
    const p = positions();
    const server = p["host:file-server-1"]!;
    const ws = p["host:corp-workstation-1"]!;
    const seg = Object.entries(p).find(([k]) => k.startsWith("segment:"))![1];
    // Centred on the drop point (B5, 2026-11-21: its top-left had been), snapped to the grid.
    expect(dist({ x: server.x + BUS_ICON / 2, y: server.y + BUS_ICON / 2 }, { x: 600, y: 400 })).toBeLessThanOrEqual(24);
    // Nothing at the origin; everything within a template's box of the server.
    for (const q of [ws, seg]) expect(dist(q, server)).toBeLessThanOrEqual(TEMPLATE_BOX);
    expect(seg.y).toBeLessThan(server.y); // the switch above its first host
    expect(ws.y).toBe(server.y); // the row continues
    expect(ws.x).toBeGreaterThan(server.x);
  });

  it("the Branch office template: router and switch where its layout says, the users in the row under the switch -- every node near the drop, no edge longer than the template's box", async () => {
    const { api } = seeded({ personas: LIB, imageProfiles: PROFILES, templates: [BRANCH] });
    identityView();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());
    quickAdd("branch", 600, 300);
    fireEvent.click(await screen.findByTestId("template-add"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").length).toBe(1));
    const p = positions();
    const gw = p["host:branch-gw"]!;
    const sw = p["host:branch-sw"]!;
    const users = p["host:branch-user"]!;
    // The template's root centred on the drop point (B5), snapped to the grid.
    expect(dist({ x: gw.x + DEVICE_W / 2, y: gw.y + DEVICE_H / 2 }, { x: 600, y: 300 })).toBeLessThanOrEqual(24);
    expect(sw).toEqual({ x: gw.x, y: gw.y + 140 });
    expect(users.y).toBeGreaterThan(sw.y);
    for (const q of [sw, users]) expect(dist(q, gw)).toBeLessThanOrEqual(TEMPLATE_BOX + 66 + 72);
    // The edges: gw - sw and sw - users.
    expect(dist(gw, sw)).toBeLessThanOrEqual(TEMPLATE_BOX);
    expect(dist(sw, users)).toBeLessThanOrEqual(TEMPLATE_BOX);
  });
});

describe("placeDropped (pure)", () => {
  const before = readTe("twin_config:\n  id: t\n  segments:\n  - { name: lan, cidr: 10.1.0.0/24, zone_type: vlan }\n  nodes:\n  - { name: gw, kind: router, image: x, nets: [ { segment: lan } ] }\n");
  it("a host dropped on a router: the router's new segment's switch below the router, the host under it; nothing is left without a pin", () => {
    const after = readTe("twin_config:\n  id: t\n  segments:\n  - { name: lan, cidr: 10.1.0.0/24, zone_type: vlan }\n  - { name: lan-2, cidr: 10.1.1.0/24, zone_type: vlan }\n  nodes:\n  - { name: gw, kind: router, image: x, nets: [ { segment: lan }, { segment: lan-2 } ] }\n  - { name: web, image: x, net: lan-2 }\n");
    const pins = placeDropped(before, after, { x: 300, y: 300 }, {}, { "host:gw": { x: 100, y: 50 } });
    expect(pins["segment:10.1.1.0/24"]).toEqual({ x: 100, y: 50 + 66 + 72 });
    expect(pins["host:web"]).toEqual({ x: 100 + 75 - 15, y: 50 + 66 + 72 + 66 + 72 });
  });
  it("a segment trunked to a real switch is carried by it (no implied switch to place)", () => {
    const te = readTe("twin_config:\n  id: t\n  segments:\n  - { name: lan, cidr: 10.1.0.0/24 }\n  nodes:\n  - { name: sw, kind: switch, image: '' }\n  - { name: gw, kind: router, image: x, nets: [ { segment: lan } ] }\n  links:\n  - { a: { device: gw, port: eth1 }, b: { device: sw, port: P1 }, mode: trunk, native: lan, vlans: [lan] }\n");
    expect(carrierOf(te, "lan")).toBe("sw");
  });
});

describe("B5: the Internet tool lands at the pointer", () => {
  it("its switch is centred on the drop point and its sink stands under it (it had always gone to the centre)", async () => {
    const { api } = seeded({ personas: LIB, imageProfiles: PROFILES });
    identityView();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(window.document.querySelector(".react-flow__pane")).not.toBeNull());
    quickAdd("internet", 900, 520);
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").length).toBe(1));
    const p = positions();
    const seg = Object.entries(p).find(([k]) => k.startsWith("segment:"))![1];
    expect(dist({ x: seg.x + DEVICE_W / 2, y: seg.y + DEVICE_H / 2 }, { x: 900, y: 520 })).toBeLessThanOrEqual(24);
    const sink = p["host:internet-sink"]!;
    expect(sink.y).toBeGreaterThan(seg.y);
    expect(dist(sink, seg)).toBeLessThanOrEqual(TEMPLATE_BOX);
  });
});
