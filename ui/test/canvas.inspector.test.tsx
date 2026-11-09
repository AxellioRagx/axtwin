/**
 * The inspector by tier (`docs/design/topology-layers.md` §7.4, track 5b,
 * 2026-11-07): a node's panel in blocks named after the tier each edit lands
 * on (Identity & placement = topology, Traffic = tfc, Taps = tap, Members,
 * Provenance); a tap's row and chip; the link row editable; the Checks tab's
 * Provenance summary filtering the canvas; the Provenance block's confirm /
 * mark-unconfirmed / assert through the assertions seam on a cast twin.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { addTapBinding, moveTapBinding, readTap, removeTapBinding, setTapSensors, tapsOn } from "../src/features/edit-canvas/edit/tap";
import { unwireTierDefault, wireTapDefault } from "../src/features/edit-canvas/edit/manifest";
import { addLink, readTe, removeLink, setLinkField, setMemberRole } from "../src/features/edit-canvas/edit/te";
import { commit, seeded, segment, settle, tab, twinYaml } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TE = `twin_config:
  id: insp
  segments:
    - { name: office, cidr: 10.71.10.0/24, source: discovered, confidence: 0.9 }
    - { name: servers, cidr: 10.71.20.0/24 }
  nodes:
    - name: corp-workstation
      role: corp-workstation
      image: debian-12-cloud@v1
      count: 2
      net: office
      members:
        - { index: 2, role: jump-host }
    - name: file-server
      role: file-server
      image: ubuntu-server-22@v5
      nets:
        - { segment: servers, ip: 10.71.20.10 }
    - name: access-sw
      kind: switch
      role: l2-switch
      source: asserted
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: office }
        - { segment: servers }
  links:
    - a: { device: gw, port: eth1 }
      b: { device: access-sw, port: SFP1 }
      mode: trunk
      native: office
      vlans: [office]
      source: asserted
    - a: { device: access-sw, port: P24 }
      b: { device: file-server, port: eth0 }
      mode: access
      vlans: [servers]
      state: down
      source: unconfirmed
`;

const TAP = `tap-plan:
  target: insp
  bindings:
    - selector: { scope: segment, target: office }
      sensors: [xpress-sw-a]
      mode: exclusive
    - selector:
        scope: port
        target: file-server
        segment: servers
      sensors:
        - xpress-sw-a
      mode: exclusive
`;

function withTwin(id: string, files: Record<string, string>, over: Parameters<typeof seeded>[0] = {}, cast = false) {
  const base = seeded();
  const template = base.state.twins[cast ? "corp-cast" : "corp-auth"]!;
  return seeded({
    twins: { ...base.state.twins, [id]: { ...template, id } },
    documents: { ...base.state.documents, [id]: { id, te_file: "te.yaml", files: { "twin.yaml": twinYaml(id), ...files }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id, description: null, cluster: null, parameterized: false, param_names: [], error: null }],
    ...over,
  });
}

/** The page validates every edit: the newest validate call carries the working document. */
const lastFiles = (state: ReturnType<typeof seeded>["state"]) => (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe("the tap plan reader and editor (tap.ts)", () => {
  it("unwires a tier from the manifest: the whole block, or only `default:` when variants stay", () => {
    const m = "twin:\n  id: insp\n  tiers:\n    te:\n      default: te.yaml\n    tap:\n      default: tap/default.yaml\n";
    expect(unwireTierDefault(m, "tap")).toBe("twin:\n  id: insp\n  tiers:\n    te:\n      default: te.yaml\n");
    const v = "twin:\n  id: insp\n  tiers:\n    tap:\n      default: tap/default.yaml\n      variants: [tap/wide.yaml]\n";
    expect(unwireTierDefault(v, "tap")).toBe("twin:\n  id: insp\n  tiers:\n    tap:\n      variants: [tap/wide.yaml]\n");
    expect(unwireTierDefault(m, "tfc")).toBe(m);
  });
  it("reads flow and block selectors, sensors and mode", () => {
    const t = readTap(TAP);
    expect(t.problems).toEqual([]);
    expect(t.target).toBe("insp");
    expect(t.bindings.map((b) => [b.index, b.scope, b.targets, b.segment, b.sensors])).toEqual([
      [0, "segment", ["office"], null, ["xpress-sw-a"]],
      [1, "port", ["file-server"], "servers", ["xpress-sw-a"]],
    ]);
    expect(tapsOn(t, "office").map((b) => b.index)).toEqual([0]);
    expect(tapsOn(t, "servers")).toEqual([]);
  });
  it("adds, moves, re-sensors and removes a binding, every other line untouched", () => {
    const added = addTapBinding(TAP, { scope: "segment", target: "servers", sensors: ["xpress-sw-a", "probe"], source: "authored" });
    expect(added.startsWith(TAP)).toBe(true);
    const t = readTap(added);
    expect(t.bindings[2]).toMatchObject({ scope: "segment", targets: ["servers"], sensors: ["xpress-sw-a", "probe"], source: "authored" });
    const moved = moveTapBinding(added, 0, "servers");
    expect(readTap(moved).bindings[0]!.targets).toEqual(["servers"]);
    expect(moved.split("\n").slice(3)).toEqual(added.split("\n").slice(3).map((l, i) => (i === 0 ? l.replace("target: office", "target: servers") : l)));
    expect(moved.split("\n").slice(0, 3)).toEqual(added.split("\n").slice(0, 3));
    expect(() => moveTapBinding(added, 1, "office")).toThrow(/port tap/);
    const sensored = setTapSensors(added, 1, ["other"]);
    expect(readTap(sensored).bindings[1]!.sensors).toEqual(["other"]);
    expect(sensored).not.toContain("        - xpress-sw-a");
    const removed = removeTapBinding(added, 0);
    expect(readTap(removed).bindings.map((b) => b.scope)).toEqual(["port", "segment"]);
    expect(readTap(removeTapBinding(removed, 5)).bindings.length).toBe(2);
  });
  it("adds into an empty `bindings: []` plan", () => {
    const empty = "tap-plan:\n  target: insp\n  bindings: []\n";
    const t = readTap(addTapBinding(empty, { scope: "port", target: "gw", segment: "office", sensors: [] }));
    expect(t.bindings).toMatchObject([{ scope: "port", targets: ["gw"], segment: "office", sensors: [] }]);
  });
});

describe("links[] and members[] editing (te.ts)", () => {
  it("adds a link, sets its fields, removes it", () => {
    const added = addLink(TE, { a: { device: "access-sw", port: "P1" }, b: { device: "corp-workstation", port: "eth0" }, mode: "access", vlans: ["office"] });
    expect(readTe(added).links.length).toBe(3);
    expect(readTe(added).links[2]).toMatchObject({ a: { device: "access-sw", port: "P1" }, mode: "access", vlans: ["office"], state: "up" });
    const trunked = setLinkField(setLinkField(added, 2, "mode", "trunk"), 2, "native", "office");
    expect(readTe(trunked).links[2]).toMatchObject({ mode: "trunk", native: "office" });
    const vlans = setLinkField(trunked, 2, "vlans", ["office", "servers"]);
    expect(readTe(vlans).links[2]!.vlans).toEqual(["office", "servers"]);
    const down = setLinkField(vlans, 2, "state", "down");
    expect(readTe(down).links[2]!.state).toBe("down");
    const up = setLinkField(down, 2, "state", null);
    expect(readTe(up).links[2]!.state).toBe("up");
    const port = setLinkField(up, 2, "a.port", "P2");
    expect(readTe(port).links[2]!.a.port).toBe("P2");
    const removed = removeLink(port, 2);
    expect(readTe(removed).links.length).toBe(2);
    expect(removed).toBe(TE);
  });
  it("sets and clears a member's role override", () => {
    const set = setMemberRole(TE, "corp-workstation", 1, "kiosk");
    expect(readTe(set).nodes.find((n) => n.name === "corp-workstation")!.members.map((m) => [m.index, m.role])).toEqual([[2, "jump-host"], [1, "kiosk"]]);
    const cleared = setMemberRole(set, "corp-workstation", 2, null);
    expect(readTe(cleared).nodes.find((n) => n.name === "corp-workstation")!.members.map((m) => [m.index, m.role])).toEqual([[1, "kiosk"]]);
    const none = setMemberRole(cleared, "corp-workstation", 1, null);
    expect(readTe(none).nodes.find((n) => n.name === "corp-workstation")!.members).toEqual([]);
    expect(none).not.toContain("members");
  });
});

describe("the inspector by tier", () => {
  it("a node's panel carries the five blocks; a member's role edits members[] as a traffic change", async () => {
    const { api, state } = withTwin("insp", { "te.yaml": TE });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    const np = await screen.findByTestId("node-panel");
    expect(np.dataset["tierBlocks"]).toBe("identity,traffic,taps,members,provenance");
    const heads = within(np).getAllByRole("heading", { level: 4 }).map((h) => h.textContent!.split("·")[0]!.trim());
    expect(heads).toEqual(["Identity & placement", "Traffic", "Members", "Provenance", "Actions"]);
    const rows = within(np).getAllByTestId("member-row");
    expect(rows.map((r) => [r.dataset["index"], r.dataset["overridden"] ?? "no"])).toEqual([["1", "no"], ["2", "true"]]);
    expect((within(rows[1]!).getByLabelText("Role of corp-workstation02") as HTMLInputElement).value).toBe("jump-host");
    expect(within(np).getByTestId("provenance-source").textContent).toBe("authored");
    expect((within(np).getByTestId("prov-confirm") as HTMLButtonElement).disabled).toBe(true);
    commit(within(rows[0]!).getByLabelText("Role of corp-workstation01"), "kiosk");
    await settle();
    const te = readTe(lastFiles(state)["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "corp-workstation")!.members.map((m) => [m.index, m.role])).toEqual([[2, "jump-host"], [1, "kiosk"]]);
  });
  it("the switch's panel shows its links; a link row opens the link panel, whose state edits links[]", async () => {
    const { api, state } = withTwin("insp", { "te.yaml": TE });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getByTestId("canvas-device"));
    const np = await screen.findByTestId("node-panel");
    expect(within(np).getByTestId("provenance-source").textContent).toBe("asserted");
    const links = within(np).getAllByTestId("device-link");
    expect(links.map((l) => l.dataset["index"])).toEqual(["0", "1"]);
    fireEvent.click(within(links[1]!).getByRole("button"));
    const lp = await screen.findByTestId("link-panel");
    expect((within(lp).getByLabelText("Link state") as HTMLInputElement).value).toBe("down");
    expect((within(lp).getByLabelText("Link mode") as HTMLInputElement).value).toBe("access");
    fireEvent.change(within(lp).getByLabelText("Link state"), { target: { value: "up" } });
    await settle();
    expect(readTe(lastFiles(state)["te.yaml"]!).links[1]!.state).toBe("up");
    fireEvent.change(within(lp).getByLabelText("Link mode"), { target: { value: "trunk" } });
    await settle();
    expect(readTe(lastFiles(state)["te.yaml"]!).links[1]!.mode).toBe("trunk");
    const office = within(screen.getByTestId("link-vlans")).getAllByRole("checkbox")[0]!;
    fireEvent.click(office);
    await settle();
    expect(readTe(lastFiles(state)["te.yaml"]!).links[1]!.vlans).toEqual(["servers", "office"]);
  });
  it("taps are chips on their pill and device; a segment's panel adds one; a chip's click opens the tap panel; Remove drops it", async () => {
    const { api, state } = withTwin("insp", { "te.yaml": TE, "tap/default.yaml": TAP });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    const chips = screen.getAllByTestId("canvas-tap");
    expect(chips.map((c) => [c.dataset["scope"], c.dataset["target"]])).toEqual([["segment", "office"], ["port", "file-server"]]);
    fireEvent.click(segment("servers"));
    const sp = await screen.findByTestId("segment-panel");
    expect(within(sp).queryAllByTestId("tap-row")).toEqual([]);
    fireEvent.change(within(sp).getByLabelText("Tap sensors"), { target: { value: "xpress-sw-a" } });
    fireEvent.click(within(sp).getByTestId("add-tap"));
    await settle();
    const plan = readTap(lastFiles(state)["tap/default.yaml"]!);
    expect(plan.bindings[2]).toMatchObject({ scope: "segment", targets: ["servers"], sensors: ["xpress-sw-a"], source: "authored" });
    await waitFor(() => expect(screen.getAllByTestId("canvas-tap").length).toBe(3));
    fireEvent.click(screen.getAllByTestId("canvas-tap").find((c) => c.dataset["target"] === "servers")!);
    const tp = await screen.findByTestId("tap-panel");
    expect(tp.textContent).toContain("segment servers");
    commit(within(tp).getByLabelText("Sensors"), "xpress-sw-a, probe");
    await settle();
    expect(readTap(lastFiles(state)["tap/default.yaml"]!).bindings[2]!.sensors).toEqual(["xpress-sw-a", "probe"]);
    fireEvent.click(within(tp).getByRole("button", { name: "Remove tap" }));
    await settle();
    expect(readTap(lastFiles(state)["tap/default.yaml"]!).bindings.length).toBe(2);
  });
  it("a tap on a twin with no tap plan creates tap/default.yaml and wires the manifest", async () => {
    const { api, state } = withTwin("insp", { "te.yaml": TE });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    expect(screen.queryAllByTestId("canvas-tap")).toEqual([]);
    fireEvent.click(segment("office"));
    const sp = await screen.findByTestId("segment-panel");
    // Add tap waits for a sensor (the tap tier refuses a binding with none); the targets' declared ones are offered.
    const add = within(sp).getByTestId("add-tap") as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect([...document.querySelectorAll("#cv-sensors option")].map((o) => o.getAttribute("value"))).toEqual(["XPress-SW-A"]);
    fireEvent.change(within(sp).getByLabelText("Tap sensors"), { target: { value: "XPress-SW-A" } });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await settle();
    const files = lastFiles(state);
    expect(readTap(files["tap/default.yaml"]!).bindings).toMatchObject([{ scope: "segment", targets: ["office"], sensors: ["XPress-SW-A"] }]);
    expect(files["twin.yaml"]).toContain("    tap:\n      default: tap/default.yaml");
  });
  it("removing the last tap drops the tap plan and its manifest entry; Undo puts both back", async () => {
    const one = `tap-plan:\n  target: insp\n  bindings:\n    - selector: { scope: boundary, target: [office, servers] }\n      sensors: [XPress-SW-A]\n      mode: exclusive\n`;
    const { api, state } = withTwin("insp", { "te.yaml": TE, "tap/default.yaml": one });
    state.documents.insp!.files["twin.yaml"] = wireTapDefault(state.documents.insp!.files["twin.yaml"]!, "tap/default.yaml");
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    // A boundary binding draws a chip on each of its two segments: one binding.
    expect(screen.getAllByTestId("canvas-tap").map((t) => t.getAttribute("data-tap"))).toEqual(["0", "0"]);
    fireEvent.click(screen.getAllByTestId("canvas-tap")[0]!);
    const panel = await screen.findByTestId("tap-panel");
    fireEvent.click(within(panel).getByRole("button", { name: "Remove tap" }));
    await settle();
    const files = lastFiles(state);
    expect(files["tap/default.yaml"]).toBeUndefined();
    expect(files["twin.yaml"]).not.toContain("tap:");
    expect(screen.queryAllByTestId("canvas-tap")).toEqual([]);
    fireEvent.keyDown(window, { key: "z", ctrlKey: true });
    await settle();
    expect(lastFiles(state)["tap/default.yaml"]).toBe(one);
    expect(lastFiles(state)["twin.yaml"]).toContain("    tap:\n      default: tap/default.yaml");
  });
  it("Checks lists the taps with no sensor, filters the canvas to them, and sets one sensor on all", async () => {
    const bare = `tap-plan:\n  target: insp\n  bindings:\n    - selector: { scope: segment, target: office }\n      mode: exclusive\n    - selector: { scope: segment, target: servers }\n      mode: exclusive\n`;
    const { api, state } = withTwin("insp", { "te.yaml": TE, "tap/default.yaml": bare });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    tab("Checks");
    const row = await screen.findByTestId("taps-need-sensor");
    expect(within(row).getByTestId("taps-need-sensor-row").textContent).toContain("2 taps need a sensor");
    fireEvent.click(within(row).getByTestId("taps-need-sensor-row"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").every((h) => h.classList.contains("cv-dim"))).toBe(true));
    expect(screen.getAllByTestId("canvas-tap").some((t) => t.classList.contains("cv-dim"))).toBe(false);
    const set = within(row).getByTestId("set-sensor-all") as HTMLButtonElement;
    expect(set.disabled).toBe(true);
    fireEvent.change(within(row).getByLabelText("Sensor for every tap without one"), { target: { value: "XPress-SW-A" } });
    fireEvent.click(set);
    await settle();
    expect(readTap(lastFiles(state)["tap/default.yaml"]!).bindings.map((b) => b.sensors)).toEqual([["XPress-SW-A"], ["XPress-SW-A"]]);
    expect(screen.queryByTestId("taps-need-sensor")).toBeNull();
  });
  it("the Checks tab's Provenance summary counts sources and a row dims the rest of the canvas", async () => {
    const { api } = withTwin("insp", { "te.yaml": TE });
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    tab("Checks");
    const sum = await screen.findByTestId("provenance-summary");
    const count = (k: string) => within(sum).getByTestId(`prov-row-${k}`).querySelector(".badge")!.textContent;
    // 2 segments + 4 nodes + 2 links: office discovered; access-sw and link 0 asserted; link 1 unconfirmed; the rest authored.
    expect([count("discovered"), count("asserted"), count("authored"), count("unconfirmed")]).toEqual(["1", "2", "4", "1"]);
    fireEvent.click(within(sum).getByTestId("prov-row-asserted"));
    await waitFor(() => expect(screen.getByTestId("canvas-device").classList.contains("cv-dim")).toBe(false));
    expect(screen.getAllByTestId("canvas-host").every((h) => h.classList.contains("cv-dim"))).toBe(true);
    expect(segment("office").classList.contains("cv-dim")).toBe(true);
    fireEvent.click(within(sum).getByTestId("prov-row-asserted"));
    await waitFor(() => expect(screen.getAllByTestId("canvas-host").some((h) => h.classList.contains("cv-dim"))).toBe(false));
  });
});

describe("the Provenance block through the seam (a cast twin)", () => {
  const ROSTER = {
    id: "insp",
    pit_id: "pit-1",
    node_roster: [
      { name: "gw", kind: "router", role: "gw", count: 1, segments: ["office", "servers"], node_ids: ["gw"], members: ["address:10.71.10.1"] },
      { name: "corp-workstation", kind: "host", role: "corp-workstation", count: 2, segments: ["office"], node_ids: ["corp-workstation01", "corp-workstation02"], members: ["address:10.71.10.2", "address:10.71.10.3"] },
      { name: "file-server", kind: "host", role: "file-server", count: 1, segments: ["servers"], node_ids: ["file-server"], members: ["address:10.71.20.10"] },
      { name: "access-sw", kind: "switch", role: "l2-switch", count: 1, segments: [], node_ids: ["access-sw"], members: ["mac:aa:bb:cc:00:00:01"] },
    ],
    segment_roster: [
      { name: "office", cidr: "10.71.10.0/24", zone_type: "vlan", observed_cidr: "10.71.10.0/24" },
      { name: "servers", cidr: "10.71.20.0/24", zone_type: "vlan", observed_cidr: null },
    ],
  };
  const castTwin = () => withTwin("insp", { "te.yaml": TE, "tap/default.yaml": TAP }, { casts: { insp: ROSTER } }, true);
  const putBody = (state: ReturnType<typeof seeded>["state"]) => state.writes.filter((w) => w.op === "put-assertions").at(-1)!.body as Record<string, unknown[]>;

  it("Split and assert asks which observed hosts the split instances are, and writes an override `field: group` on exactly those", async () => {
    const { api, state } = castTwin();
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    const np = await screen.findByTestId("node-panel");
    fireEvent.click(within(np).getByLabelText("Pick corp-workstation02"));
    fireEvent.change(within(np).getByLabelText("New group name"), { target: { value: "jumpers" } });
    const btn = within(np).getByTestId("split-assert") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    // Nothing is split or written yet: the second step names the hosts.
    const step = within(np).getByTestId("split-assert-keys");
    expect(state.writes.some((w) => w.op === "put-assertions")).toBe(false);
    const go = within(step).getByTestId("split-assert-go") as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    // One instance split off: one host, and the other box locks once it is picked. Instance 2 is not "the 2nd key".
    fireEvent.click(within(step).getByLabelText("Observed host address:10.71.10.2"));
    expect((within(step).getByLabelText("Observed host address:10.71.10.3") as HTMLInputElement).disabled).toBe(true);
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-assertions")).toBe(true));
    expect(putBody(state)["override"]).toMatchObject([{ key: "address:10.71.10.2", field: "group", value: "jumpers" }]);
    expect(putBody(state)["override"]).toHaveLength(1);
  });
  it("Split and assert no longer needs the roster to match the count; Cancel leaves everything as it was", async () => {
    const roster = { ...ROSTER, node_roster: ROSTER.node_roster.map((n) => (n.name === "corp-workstation" ? { ...n, members: ["address:10.71.10.2", "address:10.71.10.3", "address:10.71.10.4"] } : n)) };
    const { api, state } = withTwin("insp", { "te.yaml": TE, "tap/default.yaml": TAP }, { casts: { insp: roster } }, true);
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    const np = await screen.findByTestId("node-panel");
    expect(within(np).queryByTestId("split-assert-block")).toBeNull();
    fireEvent.click(within(np).getByLabelText("Pick corp-workstation01"));
    fireEvent.change(within(np).getByLabelText("New group name"), { target: { value: "jumpers" } });
    fireEvent.click(within(np).getByTestId("split-assert"));
    const step = within(np).getByTestId("split-assert-keys");
    expect(within(step).getAllByRole("checkbox")).toHaveLength(3);
    const writes = state.writes.length;
    fireEvent.click(within(step).getByRole("button", { name: "Cancel" }));
    expect(within(np).queryByTestId("split-assert-keys")).toBeNull();
    expect(state.writes.length).toBe(writes);
    expect(within(np).getAllByTestId("member-row")).toHaveLength(2);
  });
  it("Split and assert is blocked on a group the cast observed no member of", async () => {
    const roster = { ...ROSTER, node_roster: ROSTER.node_roster.map((n) => (n.name === "corp-workstation" ? { ...n, members: [] } : n)) };
    const { api } = withTwin("insp", { "te.yaml": TE, "tap/default.yaml": TAP }, { casts: { insp: roster } }, true);
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    const np = await screen.findByTestId("node-panel");
    expect(within(np).getByTestId("split-assert-block").textContent).toContain("no observed members in the cast");
  });
  it("Confirm on a node writes a confirm mark per observed member; Mark unconfirmed writes doubt and lifts the confirm", async () => {
    const { api, state } = castTwin();
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    const np = await screen.findByTestId("node-panel");
    const confirm = within(np).getByTestId("prov-confirm");
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-assertions")).toBe(true));
    let body = putBody(state);
    expect(body["schema"]).toBe("axr-assertions/2");
    expect(body["confirm"]).toMatchObject([{ key: "address:10.71.10.2" }, { key: "address:10.71.10.3" }]);
    expect(body["doubt"]).toBeUndefined();
    await screen.findByText(/promoted: confirmed: corp-workstation/);
    await settle();
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "corp-workstation")!);
    fireEvent.click(within(await screen.findByTestId("node-panel")).getByTestId("prov-doubt"));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "put-assertions").length).toBe(2));
    body = putBody(state);
    expect(body["doubt"]).toMatchObject([{ key: "address:10.71.10.2" }, { key: "address:10.71.10.3" }]);
  });
  it("Assert: a switch writes a device_kind override; a link asserts as a link add keyed by its ports; a segment with no observed network is blocked", async () => {
    const { api, state } = castTwin();
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-host").find((h) => h.dataset["nodeId"] === "file-server")!);
    const np = await screen.findByTestId("node-panel");
    fireEvent.click(within(np).getByTestId("prov-assert-switch"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-assertions")).toBe(true));
    expect(putBody(state)["override"]).toMatchObject([{ key: "address:10.71.20.10", field: "device_kind", value: "switch" }]);
    await screen.findByText(/promoted: file-server is a switch/);
    await settle();
    fireEvent.click(screen.getByTestId("canvas-device"));
    const links = within(await screen.findByTestId("node-panel")).getAllByTestId("device-link");
    fireEvent.click(within(links[1]!).getByRole("button"));
    const lp = await screen.findByTestId("link-panel");
    fireEvent.click(within(lp).getByTestId("prov-assert-link"));
    await waitFor(() => expect(state.writes.filter((w) => w.op === "put-assertions").length).toBe(2));
    const add = putBody(state)["add"] as Record<string, unknown>[];
    expect(add.at(-1)).toMatchObject({ kind: "link", key: "address:10.71.20.10#eth0|mac:aa:bb:cc:00:00:01#P24", mode: "access", state: "down" });
    expect(add.at(-1)!["vlans"]).toBeUndefined();
    fireEvent.click(segment("servers"));
    const sp = await screen.findByTestId("segment-panel");
    expect((within(sp).getByTestId("prov-confirm") as HTMLButtonElement).disabled).toBe(true);
    expect(sp.textContent).toContain("no observed network");
    fireEvent.click(segment("office"));
    expect((within(await screen.findByTestId("segment-panel")).getByTestId("prov-confirm") as HTMLButtonElement).disabled).toBe(false);
  });
  it("a tap asserts as a tap add keyed by its segment's observed network", async () => {
    const { api, state } = castTwin();
    renderAt(api, "/twins/insp/canvas");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(screen.getAllByTestId("canvas-tap").find((c) => c.dataset["target"] === "office")!);
    const tp = await screen.findByTestId("tap-panel");
    fireEvent.click(within(tp).getByTestId("prov-assert-tap"));
    await waitFor(() => expect(state.writes.some((w) => w.op === "put-assertions")).toBe(true));
    expect((putBody(state)["add"] as Record<string, unknown>[]).at(-1)).toMatchObject({ kind: "tap", key: "segment:10.71.10.0/24", sensors: ["xpress-sw-a"] });
  });
});
