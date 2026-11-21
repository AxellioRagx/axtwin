/**
 * The design canvas's test fixtures and page helpers, shared by the canvas
 * test files (`canvas.edit`, `canvas.exits`, `canvas.place`): one small
 * twin-config bundle under three ids, a deploy on one of them, and the
 * gestures a test drives the page with (open the palette, arm an item, click
 * a segment or the pane, commit a field).
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";

import type { CastRoster, DeploySummary, Imprint, Persona, TopologyExport, TwinConfigDocument } from "../../src/api/types";
import { fakeApi, type FakeState } from "./fakeApi";
import { twin } from "../imprints.page.test";

// ---------------------------------------------------------------------------
// Fixtures: one small twin-config bundle, under three ids.
// ---------------------------------------------------------------------------
export function teText(id: string): string {
  return `# ${id} -- a test twin
twin_config:
  id: ${id}
  description: a small twin for the canvas tests
  segments:
    - { name: net-lan, cidr: 10.1.0.0/24, zone_type: vlan }   # the LAN
    - { name: net-dmz, cidr: 10.1.1.0/24, zone_type: vlan }

  nodes:
    - name: gw
      role: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: net-lan }
        - { segment: net-dmz }

    - name: ws01
      role: corp-workstation
      image: win11-finance@v4
      net: net-lan

    - name: web01
      role: web-tier
      image: ubuntu-server-22@v5
      net: net-dmz
`;
}

export function twinYaml(id: string): string {
  return `twin:\n  id: ${id}\n  tiers:\n    te:\n      default: te.yaml\n    tfc:\n      default: tfc/default.yaml\n`;
}

export const TFC = `traffic-profile:\n  target: test\n  assignments:\n    - role: corp-workstation\n      persona: corp-workstation\n`;

export function document(id: string): TwinConfigDocument {
  return { id, te_file: "te.yaml", files: { "te.yaml": teText(id), "twin.yaml": twinYaml(id), "tfc/default.yaml": TFC }, skipped: [] };
}

export const PERSONAS: Persona[] = [
  { id: "corp-workstation", os_family: "windows", drive: "browser", source: "catalog", requires: [], summary: "office worker", emits: [], category: "endpoint", label: "Office user", blurb: "" },
  { id: "generic-endpoint", os_family: "linux", drive: "shell", source: "catalog", requires: [], summary: "chatter", emits: [], category: "endpoint", label: "Generic endpoint", blurb: "" },
  { id: "web-tier", os_family: "linux", drive: "real-service", source: "catalog", requires: [], summary: "web", emits: [], category: "business", label: "Web / app server", blurb: "" },
];

export const DEPLOY: DeploySummary = { deploy_id: "dep-1", scenario_id: "corp-live", status: "completed", started_at: 1700000000, updated_at: 1700000100, tier: "te", target: "dev45" };
export const IMPRINT: Imprint = { pit_id: "pit-1", name: "corp capture", derived_from: null, has_assertions: false, twins: [{ id: "corp-cast" }] };
export const CAST_EXPORT: TopologyExport = {
  imprint_id: "corp-cast", pit_id: "pit-1", as_of: 1, network: "corp", vantage: "hq", windows: 1, schema_version: "1",
  segments: [{ cidr: "10.80.20.0/24", name: "net-lan", entities: 1, coverage_source: null }],
  nodes: [{ id: "address:10.80.20.4", address: "10.80.20.4", locality: "local", segment: "10.80.20.0/24", segment_evidence_class: "observed", role: "corp-workstation", role_confidence: 0.9, role_evidence_class: "observed", windows_seen: 1, listen_ports: [], dial_ports: [] }],
  edges: [], census: { entities_total: 1, with_role: 1, placed: 1, local: 1 },
};

/** The cast roster of `corp-cast`: the group `ws01` collapsed two observed hosts, `web01` one;
 *  `net-lan` was relocated from the observed 10.80.20.0/24, `net-dmz` observed as deployed. */
export const CAST_ROSTER: CastRoster = {
  id: "corp-cast", pit_id: "pit-1",
  node_roster: [
    { name: "gw", kind: "router", role: "gw", count: 1, segments: ["net-lan", "net-dmz"], node_ids: ["gw"], members: [] },
    { name: "ws01", kind: "host", role: "corp-workstation", count: 1, segments: ["net-lan"], node_ids: ["ws01"], members: ["address:10.80.20.4", "hostname:fin-ws"] },
    { name: "web01", kind: "host", role: "web-tier", count: 1, segments: ["net-dmz"], node_ids: ["web01"], members: ["hostname:web-a"] },
  ],
  segment_roster: [
    { name: "net-lan", cidr: "10.1.0.0/24", zone_type: "vlan", observed_cidr: "10.80.20.0/24" },
    { name: "net-dmz", cidr: "10.1.1.0/24", zone_type: "vlan", observed_cidr: "10.1.1.0/24" },
  ],
};

export function seeded(over: Partial<FakeState> = {}) {
  return fakeApi({
    twins: { "corp-auth": twin("corp-auth", { hosts: 2, segments: 2 }), "corp-live": twin("corp-live", { hosts: 2, segments: 2 }), "corp-cast": twin("corp-cast", { hosts: 2, segments: 2, pit_id: "pit-1" }) },
    documents: { "corp-auth": document("corp-auth"), "corp-live": document("corp-live"), "corp-cast": document("corp-cast") },
    twinConfigs: [
      { id: "corp-auth", description: null, cluster: null, parameterized: false, param_names: [], error: null },
      { id: "corp-live", description: null, cluster: null, parameterized: false, param_names: [], error: null },
    ],
    deploys: [DEPLOY],
    personas: PERSONAS,
    imprints: [IMPRINT],
    exports: { "corp-cast": CAST_EXPORT },
    casts: { "corp-cast": CAST_ROSTER },
    ...over,
  });
}

export const hosts = () => screen.getAllByTestId("canvas-host").map((h) => h.dataset["nodeId"]).sort();
export const segments = () => screen.getAllByTestId("canvas-segment").map((s) => s.dataset["name"]).sort();
export const host = (name: string) => screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === name)!;
export const segment = (name: string) => screen.getAllByTestId("canvas-segment").find((s) => s.dataset["name"] === name)!;
/** Open a segment's panel as an operator does (segments drawn as switches, 2026-11-11): a click on its
 *  implied switch, else on the real switch that carries it and then its row under "Segments (VLANs)". */
export async function openSegment(name: string): Promise<HTMLElement> {
  const implied = screen.queryAllByTestId("canvas-segment").find((s) => s.dataset["name"] === name);
  if (implied) fireEvent.click(implied);
  else {
    let row: HTMLElement | undefined;
    for (const dev of screen.queryAllByTestId("canvas-device")) {
      fireEvent.click(dev);
      const np = await screen.findByTestId("node-panel");
      row = within(np).queryAllByTestId("carried-segment").find((r) => r.dataset["segment"] === name);
      if (row) break;
    }
    if (!row) throw new Error(`no implied switch and no switch carries ${name}`);
    fireEvent.click(within(row).getByRole("button"));
  }
  return screen.findByTestId("segment-panel");
}
export const panel = () => within(screen.getByTestId("side-panel"));
/** The inspector's tabs (one open at a time; a node click opens Selection). */
export const tab = (name: string) => {
  const t = screen.getByRole("tab", { name: new RegExp(`^${name}`) });
  if (t.getAttribute("aria-selected") !== "true") fireEvent.click(t);
};
/** The palette is the rail's "+" popover; open it when closed. */
export const openPalette = () => {
  if (!screen.queryByTestId("popover-palette")) fireEvent.click(screen.getByTestId("rail-add"));
};
/** The pane menu's "Add a segment" (the Add panel has no Segment card: a switch, a router's drag to
 *  the pane or this menu makes one). */
export const addSegment = () => {
  fireEvent.contextMenu(window.document.querySelector(".react-flow__pane")!, { clientX: 300, clientY: 200 });
  fireEvent.click(within(screen.getByTestId("context-menu")).getByRole("button", { name: "Add a segment" }));
};
/** A device card of the Add panel (`router`, `switch`, `server`, `workstation`, `ot`), the panel opened when closed. */
export const card = (id: string) => {
  openPalette();
  return screen.getAllByTestId("add-card").find((i) => i.dataset["id"] === id)!;
};
/** A tool of the Add panel (`internet`, `tap`). */
export const addTool = (id: string) => {
  openPalette();
  return screen.getAllByTestId("add-tool").find((i) => i.dataset["id"] === id)!;
};
/** The open drop popover. */
export const dropPopover = () => within(screen.getByTestId("drop-popover"));
export interface DropChoices {
  os?: string;
  role?: string;
  count?: number;
  prefix?: string;
}
/** In the open drop popover: pick the OS, role, count and prefix given, then Add. */
export function chooseAndAdd(opts: DropChoices = {}) {
  const p = dropPopover();
  if (opts.os) fireEvent.click(within(p.getByTestId("drop-os")).getByRole("button", { name: opts.os }));
  if (opts.role) fireEvent.click(p.getAllByTestId("drop-role").find((r) => r.dataset["role"] === opts.role)!);
  if (opts.count !== undefined) fireEvent.change(p.getByLabelText("Count"), { target: { value: String(opts.count) } });
  if (opts.prefix !== undefined) fireEvent.change(p.getByLabelText("Name prefix"), { target: { value: opts.prefix } });
  fireEvent.click(p.getByTestId("drop-add"));
}
/** Click a card (onto the selected segment, else the view's centre) and Add it with `opts`. */
export function addDevice(cardId: string, opts: DropChoices = {}) {
  fireEvent.click(card(cardId));
  chooseAndAdd(opts);
}
/** A button of the rail's Annotate popover, opened when closed. */
export const ann = (name: string) => {
  if (!screen.queryByTestId("popover-annotate")) fireEvent.click(screen.getByTestId("rail-annotate"));
  return within(screen.getByTestId("popover-annotate")).getByRole("button", { name });
};
/** A click on the empty pane. React Flow's selection mode dispatches a pane
 *  click from pointer-down + pointer-up at the same spot, not from `click`. */
export const clickPane = (x = 300, y = 700) => {
  const pane = window.document.querySelector(".react-flow__pane")!;
  fireEvent.pointerDown(pane, { button: 0, clientX: x, clientY: y, pointerId: 1, isPrimary: true, pointerType: "mouse" });
  fireEvent.pointerUp(pane, { button: 0, clientX: x, clientY: y, pointerId: 1, isPrimary: true, pointerType: "mouse" });
};
export const commit = (input: HTMLElement, value: string) => {
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
};
export const settle = () => act(() => new Promise((r) => setTimeout(r, 350)));
/** The bar's Check (on demand): run it and wait for its answer; `expect` asserts the badge's status after. */
export async function check(want?: "valid" | "invalid" | "unchecked" | "changed") {
  fireEvent.click(screen.getByTestId("check-run"));
  await waitFor(() => expect(screen.getByTestId("check-badge").dataset["status"]).not.toBe("checking"));
  if (want) await waitFor(() => expect(screen.getByTestId("check-badge").dataset["status"]).toBe(want));
}
/** The Check badge's status (`unchecked`, `valid`, `invalid`, `changed`, `checking`). */
export const checkStatus = () => screen.getByTestId("check-badge").dataset["status"];
/** A button once it is enabled. The exits that overwrite a twin wait on a passing Check of the files
 *  on screen, so -- as an operator does -- Check runs first when the files are not the ones last checked. */
export const enabled = async (name: string) => {
  const badge = screen.queryByTestId("check-badge");
  if (badge && badge.dataset["status"] !== "valid" && badge.dataset["status"] !== "checking") await check();
  await waitFor(() => expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(false));
  return screen.getByRole("button", { name });
};

