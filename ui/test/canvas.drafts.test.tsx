/**
 * Drafts and the on-demand Check (`ui/src/features/edit-canvas/SPEC.md` "The edit model",
 * `api/Spec.md` A3d): a draft opens on the canvas labelled and undeployable; Replace <origin>
 * needs a passing Check of unchanged files AND an explicit confirm; a finding clicked in the
 * Checks tab selects its element; findings map to the device, segment, link or tap they name;
 * drafts are labelled in Open and on the Twins list.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { findingPins, findingTarget } from "../src/features/edit-canvas/edit/check";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { check, checkStatus, commit, document, host, panel, seeded, segment, tab } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => localStorage.clear());

/** `corp-auth` kept as the draft `corp-auth-draft` (its id renamed, as Save as draft writes it). */
function withDraft(over: { badImage?: boolean } = {}) {
  const base = document("corp-auth");
  const te = base.files["te.yaml"]!.replace("id: corp-auth", "id: corp-auth-draft").replace(over.badImage ? "image: ubuntu-server-22@v5" : "\u0000", "image: ubuntu-server-99@v1");
  const files = { ...base.files, "te.yaml": te, "twin.yaml": base.files["twin.yaml"]!.replace("id: corp-auth", "id: corp-auth-draft") };
  return seeded({ drafts: { "corp-auth-draft": { document: { ...base, id: "corp-auth-draft", files }, origin: "corp-auth", last_check: null, created_at: "2026-11-20T00:00:00Z", updated_at: "2026-11-20T00:00:00Z" } } });
}

describe("a draft on the canvas", () => {
  it("is labelled, never deployable, and saves to itself without a gate", async () => {
    const { api, state } = withDraft({ badImage: true });
    renderAt(api, "/canvas/drafts/corp-auth-draft");
    await screen.findAllByTestId("canvas-host");
    expect(screen.getByTestId("draft-label").textContent).toBe("draft");
    // The canvas frame (its one top bar holds the title and the exits), as on any canvas route.
    expect(window.document.querySelector(".frame.canvas-frame")).not.toBeNull();
    expect(screen.getByTestId("state-pill").textContent).toContain("draft of corp-auth");
    expect(screen.queryByTestId("deploy-open")).toBeNull();
    // A failing draft: Replace stays closed; Save draft writes the draft only.
    await check("invalid");
    expect((screen.getByTestId("replace-origin") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "kiosk");
    fireEvent.click(await waitFor(() => { const b = screen.getByTestId("save-draft") as HTMLButtonElement; expect(b.disabled).toBe(false); return b; }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-draft" && w.id === "corp-auth-draft")).toBe(true));
    expect(state.writes.some((w) => w.op === "put-twin-config" || w.op === "create-twin-config")).toBe(false);
    expect(state.documents["corp-auth"]!.files["te.yaml"]).toBe(document("corp-auth").files["te.yaml"]);
  });

  it("Replace <origin> needs a passing Check of unchanged files and a confirm; it writes the origin with its own id", async () => {
    const { api, state } = withDraft();
    renderAt(api, "/canvas/drafts/corp-auth-draft");
    await screen.findAllByTestId("canvas-host");
    const replace = () => screen.getByTestId("replace-origin") as HTMLButtonElement;
    expect(replace().textContent).toBe("Replace corp-auth");
    expect(replace().disabled).toBe(true); // unchecked
    await check("valid");
    expect(replace().disabled).toBe(false);
    // An edit after the Check closes it again.
    fireEvent.click(host("ws01"));
    commit(within(await panel().findByTestId("node-panel")).getByLabelText("Role"), "kiosk");
    await waitFor(() => expect(checkStatus()).toBe("changed"));
    expect(replace().disabled).toBe(true);
    await check("valid");
    // The click only asks; Cancel writes nothing.
    fireEvent.click(replace());
    const dlg = await screen.findByTestId("replace-dialog");
    expect(dlg.textContent).toContain("corp-auth is overwritten with this draft");
    fireEvent.click(within(dlg).getByRole("button", { name: "Cancel" }));
    expect(state.writes.some((w) => w.op === "put-twin-config")).toBe(false);
    // Confirmed: the origin's own PUT, the document renamed back to the origin's id.
    fireEvent.click(replace());
    fireEvent.click(within(await screen.findByTestId("replace-dialog")).getByTestId("replace-confirm"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-twin-config")).toBe(true));
    const put = state.writes.find((w) => w.op === "put-twin-config")!;
    expect(put.id).toBe("corp-auth");
    const te = readTe((put.body as { files: Record<string, string> }).files["te.yaml"]!);
    expect(te.id).toBe("corp-auth");
    expect(te.nodes.find((n) => n.name === "ws01")!.role).toBe("kiosk");
    expect(state.drafts["corp-auth-draft"]).toBeTruthy(); // the draft is kept
    await screen.findByText(/replaced corp-auth with this draft/);
  });
});

describe("findings on the canvas", () => {
  it("a finding clicked in the Checks tab selects the element it names", async () => {
    const { api } = seeded();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(host("web01"));
    const p = await panel().findByTestId("node-panel");
    fireEvent.change(within(p).getByLabelText("Image"), { target: { value: "ubuntu-server-99@v1" } });
    fireEvent.blur(within(p).getByLabelText("Image"));
    await check("invalid");
    expect(within(host("web01")).getByTestId("finding-badge").dataset["key"]).toBe("node:web01");
    expect(within(segment("net-lan")).queryByTestId("finding-badge")).toBeNull();
    tab("Checks");
    const row = screen.getAllByTestId("finding").find((f) => f.dataset["rule"] === "IMG-010")!;
    fireEvent.click(within(row).getByRole("button"));
    await waitFor(() => expect(host("web01").classList.contains("selected")).toBe(true));
    expect(host("ws01").classList.contains("selected")).toBe(false);
    // The page's selection is the device: the Selection tab opens on its panel.
    tab("Selection");
    expect(await screen.findByTestId("node-panel")).toBeTruthy();
  });

  it("maps a finding to the device, segment, link or tap it names (an instance name to its group)", () => {
    const te = `twin_config:
  id: t
  segments:
    - { name: office, cidr: 10.2.1.0/24 }
    - { name: servers, cidr: 10.2.2.0/24 }
  nodes:
    - { name: gw, kind: router, image: r, nets: [ { segment: office }, { segment: servers } ] }
    - { name: sw1, kind: switch, image: s }
    - { name: web, image: u, count: 3, net: servers }
  links:
    - { a: { device: gw, port: p1 }, b: { device: sw1, port: p1 }, mode: trunk, vlans: [office] }
`;
    const tap = "tap-plan:\n  target: t\n  bindings:\n    - { selector: { scope: segment, target: office }, mode: exclusive, sensors: [] }\n";
    const m = readDoc({ id: "t", teFile: "te.yaml", files: { "te.yaml": te, "tap/default.yaml": tap } });
    const f = (rule: string, message: string, location: string | null = null) => ({ rule_id: rule, severity: "error", message, location });
    expect(findingTarget(f("CV-012", "x", "twin_config.segments[servers]"), m)).toEqual({ kind: "segment", name: "servers" });
    expect(findingTarget(f("CV-044", "x", "twin_config.links[0]"), m)).toEqual({ kind: "link", index: 0 });
    expect(findingTarget(f("NET-120", "host web02 has no address", "layers.infra.nodes[4]"), m)).toEqual({ kind: "node", name: "web" });
    expect(findingTarget(f("IMG-010", "node 'sw1': image 's' has no manifest", "layers.infra.nodes[1]"), m)).toEqual({ kind: "node", name: "sw1" });
    expect(findingTarget(f("NET-130", "10.2.2.0/24 overlaps", "layers.infra.networks[1]"), m)).toEqual({ kind: "segment", name: "servers" });
    expect(findingTarget(f("TIER-LOAD", "binding 0 names no sensor", "tap/default.yaml"), m)).toEqual({ kind: "tap", index: 0 });
    expect(findingTarget(f("STR-001", "the document is not a twin"), m)).toBeNull();
    const pins = findingPins([f("CV-044", "a", "twin_config.links[0]"), { ...f("W", "b", "twin_config.links[0]"), severity: "warning" }], m);
    expect(pins.get("link:0")).toEqual({ severity: "error", messages: ["CV-044: a", "W: b"] });
  });
});

describe("drafts in the lists", () => {
  it("the Twins list shows drafts labelled, with their origin, no Deploy; Open lists them too", async () => {
    const { api } = withDraft();
    renderAt(api, "/twins");
    const row = await screen.findByTestId("draft-row");
    expect(within(row).getByTestId("draft-label").textContent).toBe("draft");
    expect(row.textContent).toContain("draft of corp-auth");
    expect((within(row).getByRole("button", { name: "Deploy" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(row).getByRole("link", { name: "corp-auth-draft" }).getAttribute("href")).toBe("/canvas/drafts/corp-auth-draft");
    // The twin rows are only the twins.
    expect(screen.getAllByTestId("twin-row").map((r) => r.textContent).some((t) => t!.includes("corp-auth-draft"))).toBe(false);
  });

  it("Open (Ctrl+O on the canvas) lists the drafts under the twins", async () => {
    const { api } = withDraft();
    renderAt(api, "/twins/corp-auth/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.keyDown(window, { key: "o", ctrlKey: true });
    const dlg = await screen.findByTestId("open-dialog");
    const row = await within(dlg).findByTestId("draft-row");
    expect(within(row).getByTestId("draft-label")).toBeTruthy();
  });
});
