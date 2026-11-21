/**
 * The canvas Add panel, phase 1 (`docs/design/canvas-add-panel.md` §3, §4, §10 "Automated (phase 1)"):
 * six device cards, Tools, Recent and no Images / "From other twins"; one search ("file" -> Server
 * with file-server preselected); the OS filtering the roles; a Server drop writing the profile's
 * image and the persona; the needs hint and its one-click peers; a drop and its popover choices as
 * one undo step; the drag, click and double-click paths; recent and favourites; the health line.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Persona } from "../src/api/types";
import { applyDeviceDrop, applyInternetDrop } from "../src/features/edit-canvas/add/drop";
import { cardForPersona, defaultRole, emitsSentence, healthHints, imageFor, missingPeers, peerOffer, personaCategory, profileFor, pushRecent, readRefs, RECENT_KEY, rolesFor, searchAdd, toggleFavourite } from "../src/features/edit-canvas/add/catalog";
import { readDoc } from "../src/features/edit-canvas/edit/document";
import { readTe } from "../src/features/edit-canvas/edit/te";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";
import { PALETTE_MIME } from "../src/features/edit-canvas/add/AddContext";
import { card, check, chooseAndAdd, dropPopover, enabled, hosts, openPalette, seeded, segment, segments, settle } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
});

const p = (id: string, os: "linux" | "windows", category: string | undefined, label: string, requires: string[] = [], blurb = ""): Persona => ({
  id, os_family: os, drive: "scripted", source: "catalog", requires, summary: "", emits: [], ...(category ? { category } : {}), label, blurb,
});

/** A slice of the catalog's persona library, with the shipped categories, labels and blurbs. */
const LIB: Persona[] = [
  p("file-server", "linux", "business", "File server", ["corp-workstation"], "Serves SMB/NFS shares to workstations; bursty in business hours."),
  p("web-tier", "linux", "business", "Web / app server", [], "Serves HTTPS and queries the database tier."),
  p("egress-proxy", "linux", "network", "Web proxy"),
  p("domain-controller", "windows", "identity", "Domain controller", ["corp-workstation"], "Active Directory (AD) domain controller: DNS, Kerberos and LDAP."),
  p("corp-workstation", "windows", "endpoint", "Office user", ["domain-controller"], "A domain-joined Windows desktop."),
  p("power-user-dev", "linux", "endpoint", "Developer"),
  p("plc", "linux", "ot", "PLC", ["hmi"]),
  p("hmi", "linux", "ot", "HMI"),
  { ...p("loadgen-l1", "linux", undefined, "loadgen-l1"), label: undefined },
];

const PROFILES = [
  { name: "linux-server", image: "debian-12-cloud@v1" },
  { name: "linux-client", image: "debian-12-cloud@v1" },
  { name: "windows-server", image: "windows-server-2022@v1" },
  { name: "windows-client", image: "win11-finance@v4" },
  { name: "router", image: "debian-12-cloud@v1" },
];

/** A twin with one router and one LAN, nothing else: the needs hint has something to say. */
const BARE = `twin_config:
  id: bare
  segments:
    - { name: lan, cidr: 10.9.0.0/24, zone_type: vlan }
  nodes:
    - name: gw
      kind: router
      image: debian-12-cloud@v1
      nets:
        - { segment: lan }
`;

function bare() {
  const base = seeded();
  return seeded({
    personas: LIB,
    twins: { ...base.state.twins, bare: { ...base.state.twins["corp-auth"]!, id: "bare" } },
    documents: { ...base.state.documents, bare: { id: "bare", te_file: "te.yaml", files: { "te.yaml": BARE, "twin.yaml": "twin:\n  id: bare\n  tiers:\n    te:\n      default: te.yaml\n" }, skipped: [] } },
    twinConfigs: [...base.state.twinConfigs, { id: "bare", description: null, cluster: null, parameterized: false, param_names: [], error: null }],
  });
}

const files = (state: ReturnType<typeof seeded>["state"]) => async () => {
  await check();
  return (state.writes.filter((w) => w.op === "validate").at(-1)!.body as { files: Record<string, string> }).files;
};

describe("the Add panel (phase 1, §10)", () => {
  it("shows exactly six device cards, Tools, Recent and the health line -- no Images, no 'From other twins'", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    openPalette();
    const panel = within(screen.getByTestId("palette"));
    expect(panel.getAllByTestId("add-card").map((c) => c.dataset["id"])).toEqual(["router", "l3switch", "switch", "server", "workstation", "ot"]);
    expect(panel.getAllByTestId("add-tool").map((c) => c.dataset["id"])).toEqual(["internet", "tap"]);
    expect(panel.getByTestId("add-recent")).toBeTruthy();
    expect(panel.getByTestId("add-health").textContent).toBe("no design hints");
    const text = screen.getByTestId("palette").textContent ?? "";
    expect(text).not.toMatch(/Images|From other twins|Personas/);
    expect(text).not.toContain("debian-12-cloud");
    expect(panel.queryByText("Attacker")).toBeNull(); // no attack-framework registry: not offered
  });

  it("search 'file' -> Server with file-server preselected; 'AD' -> the domain controller; a hit's click opens the popover with it chosen", async () => {
    expect(searchAdd("file", LIB)[0]).toMatchObject({ kind: "device", card: "server", role: "file-server", os: "linux" });
    expect(searchAdd("AD", LIB)[0]).toMatchObject({ card: "server", role: "domain-controller", os: "windows" });
    expect(searchAdd("plc", LIB)[0]).toMatchObject({ card: "ot", role: "plc" });
    expect(searchAdd("office", LIB)[0]).toMatchObject({ card: "workstation", role: "corp-workstation", os: "windows" });
    expect(searchAdd("loadgen", LIB)).toEqual([]); // traffic generators are Advanced-only
    expect(searchAdd("router", LIB)[0]).toMatchObject({ kind: "device", card: "router", role: null });
    expect(searchAdd("tap", LIB)[0]).toMatchObject({ kind: "tool", tool: "tap" });

    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    openPalette();
    fireEvent.change(screen.getByLabelText("Search the Add panel"), { target: { value: "file" } });
    const first = screen.getAllByTestId("add-hit")[0]!;
    expect(first.dataset["id"]).toBe("server");
    expect(first.dataset["role"]).toBe("file-server");
    fireEvent.click(segment("lan"));
    fireEvent.click(first);
    const pop = dropPopover();
    expect(pop.getAllByTestId("drop-role").find((r) => r.getAttribute("aria-checked") === "true")!.dataset["role"]).toBe("file-server");
    expect(within(pop.getByTestId("drop-os")).getByRole("button", { name: "Linux" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("Windows filters the roles to os_family windows; Linux back again", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("server"));
    const pop = dropPopover();
    const shown = () => pop.getAllByTestId("drop-role").map((r) => `${r.dataset["role"]}:${r.dataset["os"]}`);
    expect(shown()).toEqual(["file-server:linux", "web-tier:linux", "egress-proxy:linux"]);
    fireEvent.click(within(pop.getByTestId("drop-os")).getByRole("button", { name: "Windows Server" }));
    expect(shown()).toEqual(["domain-controller:windows"]);
    // Grouped by category, each row a label and one sentence.
    expect(pop.getByText("Identity & infrastructure")).toBeTruthy();
    expect(pop.getAllByTestId("drop-role")[0]!.textContent).toContain("Active Directory (AD) domain controller");
    // The traffic generators only under Advanced.
    fireEvent.click(within(pop.getByTestId("drop-os")).getByRole("button", { name: "Linux" }));
    expect(shown()).not.toContain("loadgen-l1:linux");
    fireEvent.click(pop.getByLabelText("Show traffic generators"));
    expect(shown()).toContain("loadgen-l1:linux");
    expect(rolesFor("workstation", "windows", LIB).flatMap((g) => g.personas.map((x) => x.id))).toEqual(["corp-workstation"]);
  });

  it("dropping a Server writes a host with the profile's image and the persona, as one undo step", async () => {
    const { api, state } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("server"));
    // Every choice in the popover, then Add: one command.
    chooseAndAdd({ os: "Windows Server", role: "domain-controller", prefix: "dc" });
    await waitFor(() => expect(hosts()).toEqual(["dc-1"]));
    const f = await files(state)();
    const te = readTe(f["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "dc-1")).toMatchObject({ kind: "host", role: "domain-controller", image: "windows-server-2022@v1", nets: [{ segment: "lan", ip: "10.9.0.10" }], source: "authored" });
    const tfc = readTfc(f["tfc/default.yaml"]!);
    expect(tfc.assignments.find((a) => a.by === "node" && a.target === "dc-1")?.persona).toBe("domain-controller");
    expect(f["twin.yaml"]).toContain("tfc/default.yaml"); // the manifest wired to the new profile
    // One undo puts everything back: the host, the persona binding, the profile, the manifest.
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryAllByTestId("canvas-host")).toEqual([]));
    const back = await files(state)();
    expect(back["tfc/default.yaml"]).toBeUndefined();
    expect(back["te.yaml"]).toBe(BARE);
  });

  it("a role with unmet requires shows the needs hint; its button adds the peers on the same segment, in the same undo step", async () => {
    const { api, state } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("server"));
    const pop = dropPopover();
    fireEvent.click(pop.getAllByTestId("drop-role").find((r) => r.dataset["role"] === "file-server")!);
    expect(pop.getByTestId("drop-needs").textContent).toContain("needs workstations");
    const add = pop.getByTestId("drop-needs-add");
    expect(add.textContent).toBe("Add 5 workstations");
    fireEvent.click(add);
    // The peer is chosen; corp-workstation in turn needs a domain controller, offered next.
    expect(pop.getAllByTestId("drop-peer").map((x) => x.textContent)).toEqual([expect.stringContaining("5 × corp-workstation")]);
    expect(pop.getByTestId("drop-needs-add").textContent).toBe("Add 1 Domain controller");
    fireEvent.click(pop.getByTestId("drop-add"));
    await waitFor(() => expect(hosts()).toEqual(["corp-workstation-1", "file-server-1"]));
    const te = readTe((await files(state)())["te.yaml"]!);
    expect(te.nodes.find((n) => n.name === "corp-workstation-1")).toMatchObject({ count: 5, role: "corp-workstation", image: "win11-finance@v4", nets: [{ segment: "lan", ip: null }] });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.queryAllByTestId("canvas-host")).toEqual([]));
  });

  it("Esc keeps the defaults (the drop is written); Cancel drops nothing", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("workstation"));
    fireEvent.click(dropPopover().getByTestId("drop-cancel"));
    await settle();
    expect(screen.queryAllByTestId("canvas-host")).toEqual([]);
    fireEvent.click(segment("lan"));
    fireEvent.click(card("workstation"));
    fireEvent.keyDown(window.document, { key: "Escape" });
    await waitFor(() => expect(hosts()).toEqual(["corp-workstation-1"]));
    expect(screen.queryByTestId("drop-popover")).toBeNull();
  });

  it("a card dragged onto the empty pane opens the popover there; a host gets a segment of its own", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    const item = { kind: "device", id: "workstation", card: "workstation", label: "Workstation" };
    const dataTransfer = { getData: (t: string) => (t === PALETTE_MIME ? JSON.stringify(item) : ""), setData() {}, dropEffect: "", effectAllowed: "" };
    fireEvent.drop(window.document.querySelector(".react-flow__pane")!, { dataTransfer, clientX: 420, clientY: 360 });
    expect(screen.getByTestId("drop-popover").textContent).toContain("on a new segment");
    chooseAndAdd({ os: "Linux", count: 4 });
    await waitFor(() => expect(segments()).toEqual(["lan", "power-user-dev-lan"]));
  });

  it("a double-click on the empty canvas opens the quick-add search at the pointer; a pick drops there", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.doubleClick(window.document.querySelector(".react-flow__pane")!, { clientX: 200, clientY: 150 });
    const quick = within(await screen.findByTestId("quick-add"));
    fireEvent.change(quick.getByLabelText("Quick add search"), { target: { value: "file" } });
    fireEvent.click(quick.getAllByTestId("add-hit")[0]!);
    expect(screen.queryByTestId("quick-add")).toBeNull();
    expect(dropPopover().getAllByTestId("drop-role").find((r) => r.getAttribute("aria-checked") === "true")!.dataset["role"]).toBe("file-server");
    fireEvent.click(dropPopover().getByTestId("drop-add"));
    await waitFor(() => expect(hosts()).toEqual(["file-server-1"]));
  });

  it("recent and favourites live in this browser; the health line counts roles missing peers and bare segments", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("server"));
    chooseAndAdd({ role: "file-server" });
    await waitFor(() => expect(hosts()).toEqual(["file-server-1"]));
    expect(readRefs(RECENT_KEY)).toEqual([{ kind: "device", card: "server", role: "file-server", os: "linux", label: "File server" }]);
    // The panel reopened lists it; a star pins a card.
    fireEvent.keyDown(window, { key: "Escape" });
    openPalette();
    const panel = within(screen.getByTestId("palette"));
    expect(panel.getAllByTestId("add-recent-item").map((x) => x.textContent)).toEqual([expect.stringContaining("File server")]);
    expect(panel.getByTestId("add-health").textContent).toBe("1 role missing peers");
    fireEvent.click(panel.getByRole("button", { name: "Pin Switch" }));
    expect(panel.getAllByTestId("add-recent-item").map((x) => x.dataset["id"])).toEqual(["switch", "server"]);
    expect(JSON.parse(localStorage.getItem("axr-add-favourites")!)).toEqual([{ kind: "device", card: "switch", role: null, os: null, label: "Switch" }]);
  });

  it("works with storage unavailable (private window): recent and favourites are simply empty", () => {
    const get = Storage.prototype.getItem;
    const set = Storage.prototype.setItem;
    Storage.prototype.getItem = () => {
      throw new Error("blocked");
    };
    Storage.prototype.setItem = () => {
      throw new Error("blocked");
    };
    try {
      expect(readRefs(RECENT_KEY)).toEqual([]);
      expect(() => pushRecent([], { kind: "tool", tool: "tap", label: "Sensor / tap" })).not.toThrow();
    } finally {
      Storage.prototype.getItem = get;
      Storage.prototype.setItem = set;
    }
  });

  it("a Save after drops passes Check: the vocabulary is today's twin-config", async () => {
    const { api } = bare();
    renderAt(api, "/twins/bare/canvas");
    await screen.findAllByTestId("canvas-router");
    fireEvent.click(segment("lan"));
    fireEvent.click(card("ot"));
    chooseAndAdd({ role: "plc", count: 3 });
    await waitFor(() => expect(hosts()).toEqual(["plc-1"]));
    await check("valid");
    expect((await enabled("Save")).hasAttribute("disabled")).toBe(false);
  });
});

describe("the Add panel's pure pieces", () => {
  it("falls back to traffic / other when a persona names no category (§4.2)", () => {
    expect(personaCategory({ id: "loadgen-l9" })).toBe("traffic");
    expect(personaCategory({ id: "xseg-a-to-b" })).toBe("traffic");
    expect(personaCategory({ id: "mystery" })).toBe("other");
    expect(personaCategory({ id: "mystery", category: "nonsense" })).toBe("other");
    expect(cardForPersona({ id: "plc", category: "ot" })).toBe("ot");
    expect(emitsSentence({ emits: [{ proto: "smb", ports: [445], peer_role: "corp-workstation", transport: "tcp", cadence: "diurnal", volume: "high", optional: false, peer_scope: "one" }] })).toBe("SMB with corp-workstation; business hours.");
  });

  it("resolves card + OS to an image profile (§4.3); a missing profile resolves to none", () => {
    expect(profileFor("server", "windows")).toBe("windows-server");
    expect(profileFor("workstation", "linux")).toBe("linux-client");
    expect(profileFor("ot", null)).toBe("linux-server");
    expect(profileFor("router", null)).toBe("router");
    expect(profileFor("switch", null)).toBeNull();
    expect(imageFor(PROFILES, "windows-client")).toBe("win11-finance@v4");
    expect(imageFor([{ name: "linux-client", image: null }], "linux-client")).toBeNull();
    expect(defaultRole("server", "windows", LIB)).toBe("domain-controller");
    expect(defaultRole("workstation", "linux", LIB)).toBe("power-user-dev");
  });

  it("needs: missing peer roles and what the one click adds", () => {
    const te = readTe(BARE);
    const fs = LIB.find((x) => x.id === "file-server")!;
    expect(missingPeers(fs, te)).toEqual(["corp-workstation"]);
    expect(missingPeers(fs, te, ["corp-workstation"])).toEqual([]);
    expect(peerOffer("corp-workstation", LIB)).toMatchObject({ card: "workstation", os: "windows", persona: "corp-workstation", count: 5, label: "Add 5 workstations" });
    expect(peerOffer("dns-resolver", LIB)).toMatchObject({ card: "server", persona: null, count: 1, label: "Add 1 dns-resolver" });
  });

  it("the health line: segments with no router and roles missing peers", () => {
    const te = readTe(`twin_config:\n  id: h\n  segments:\n    - { name: a, cidr: 10.1.0.0/24 }\n    - { name: b, cidr: 10.1.1.0/24 }\n  nodes:\n    - { name: gw, kind: router, image: x, nets: [{ segment: a }] }\n    - { name: fs, role: file-server, image: x, net: b }\n`);
    expect(healthHints(te, LIB)).toEqual(["1 role missing peers", "1 segment with no router"]);
  });

  it("switch, router and internet drops write today's vocabulary", () => {
    const doc = { id: "bare", teFile: "te.yaml", files: { "te.yaml": BARE } };
    const m = readDoc(doc);
    // A managed switch on the pane: its own new segment, imageless, rstp.
    const sw = readTe(applyDeviceDrop(doc, m, { card: "switch", os: null, role: null, persona: null, count: 1, prefix: "sw", target: { pane: true }, image: null, switchType: "managed" }).files["te.yaml"]!);
    expect(sw.segments.map((s) => `${s.name}:${s.cidr}`)).toEqual(["lan:10.9.0.0/24", "switch-lan:10.9.1.0/24"]);
    expect(sw.nodes.find((n) => n.name === "sw-1")).toMatchObject({ kind: "switch", image: "", nets: [{ segment: "switch-lan", ip: null }] });
    expect(applyDeviceDrop(doc, m, { card: "switch", os: null, role: null, persona: null, count: 1, prefix: "sw", target: { pane: true }, image: null, switchType: "managed" }).files["te.yaml"]).toContain("stp: rstp");
    // A plain switch on a segment: the segment's implied switch made real.
    const ins = readTe(applyDeviceDrop(doc, m, { card: "switch", os: null, role: null, persona: null, count: 1, prefix: "sw", target: { segment: "lan" }, image: null, switchType: "plain" }).files["te.yaml"]!);
    expect(ins.links.map((l) => `${l.a.device}>${l.b.device}:${l.mode}`)).toEqual(["gw>sw-lan:trunk"]);
    // An edge firewall: the internet segment and its sink made, the firewall uplinked to it.
    const fw = readTe(applyDeviceDrop(doc, m, { card: "router", os: null, role: null, persona: null, count: 1, prefix: "edge", target: { pane: true }, image: "debian-12-cloud@v1", routerType: "firewall", placement: "edge" }).files["te.yaml"]!);
    expect(fw.nodes.find((n) => n.name === "edge-1")).toMatchObject({ kind: "router", role: "firewall", nets: [{ segment: "internet", ip: null }] });
    expect(fw.nodes.find((n) => n.name === "internet-sink")).toMatchObject({ role: "internet-sink", nets: [{ segment: "internet" }] });
    expect(fw.segments.find((s) => s.name === "internet")?.cidr).toBe("10.250.0.0/24");
    // The Internet tool dropped on a router: the same segment, the router's uplink leg on it.
    const inet = readTe(applyInternetDrop(doc, m, { router: "gw" }, "debian-12-cloud@v1").files["te.yaml"]!);
    expect(inet.nodes.find((n) => n.name === "gw")!.nets.map((l) => l.segment)).toEqual(["lan", "internet"]);
  });

  it("favourites toggle", () => {
    const ref = { kind: "tool" as const, tool: "internet" as const, label: "Internet" };
    expect(toggleFavourite(toggleFavourite([], ref), ref)).toEqual([]);
  });
});
