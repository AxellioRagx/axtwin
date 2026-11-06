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
  { id: "corp-workstation", os_family: "windows", drive: "browser", source: "catalog", requires: [], summary: "office worker", emits: [] },
  { id: "generic-endpoint", os_family: "linux", drive: "shell", source: "catalog", requires: [], summary: "chatter", emits: [] },
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
export const item = (kind: string, id: string) => {
  openPalette();
  return screen.getAllByTestId("palette-item").find((i) => i.dataset["kind"] === kind && i.dataset["id"] === id)!;
};
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
export const enabled = async (name: string) => {
  await waitFor(() => expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(false));
  return screen.getByRole("button", { name });
};

