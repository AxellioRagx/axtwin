/**
 * Emulated instances and traffic lines in the edit model (design
 * `docs/design/traffic-generation-trex-astf.md` §4.1, §12 phase 3): setting
 * Emulated writes the tfc profile only (te byte-identical); a line's badge and
 * E-W / N-S tag follow its ends; the row reasons name TGN rules and the
 * planner's one-generator limit; "no traffic" leaves real VMs undriven.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { Persona } from "../src/api/types";
import { addLine, readDoc, removeLine, setBudget, setEmulated, setExternalGroup, setLine, setNoTraffic, type WorkingDoc } from "../src/features/edit-canvas/edit/document";
import { readTfc } from "../src/features/edit-canvas/edit/tfc";
import { groupBadge, groupInstances, hasNoTraffic, lineReasons, lineTag, mixesFor, plannedGenerators, trafficHealth } from "../src/features/edit-canvas/edit/traffic";

const DIR = resolve(__dirname, "../../catalog/twins/three-tier-dc-manual");
const read = (p: string) => readFileSync(resolve(DIR, p), "utf8");
const FILES = { "te.yaml": read("te.yaml"), "twin.yaml": read("twin.yaml"), "tfc/default.yaml": read("tfc/default.yaml"), "tap/default.yaml": read("tap/default.yaml") };
const GATE = read("tfc/trafficgen.yaml");

const mix = (id: string, role: string): Persona => ({
  id, os_family: "linux", drive: "trex-astf", source: "catalog", requires: [], summary: id, category: "traffic",
  emits: [{ proto: "tcp", ports: [80], peer_role: role, transport: "tcp", cadence: "", volume: "", optional: false, peer_scope: "" }],
  mix: [{ app: "x", weight: 1 }],
});
const PERSONAS: Persona[] = [mix("web-browse", "web-tier"), mix("sql-oltp", "db-tier"),
  { id: "web-tier", os_family: "linux", drive: "agent", source: "catalog", requires: [], summary: "", emits: [] }];

function doc(files: Record<string, string> = FILES): WorkingDoc {
  return { id: "three-tier-dc-manual", teFile: "te.yaml", files: { ...files } };
}
function step(d: WorkingDoc, f: (d: WorkingDoc, m: ReturnType<typeof readDoc>) => Record<string, string>): WorkingDoc {
  return { ...d, files: f(d, readDoc(d)) };
}
const USERS = { group: "internet-users", segment: "internet", role: "internet-user", count: 200 };

describe("trafficgen in the edit model", () => {
  it("reads the gate profile's budget, emulated and lines", () => {
    const t = readTfc(GATE);
    expect(t.budget).toEqual({ cps: 100, mbps: 60 });
    expect(t.emulated.map((e) => [e.group, e.count, e.external])).toEqual([["rack1-web", 20, false], ["rack3-db", 1, false], ["rack2-web", 5, false], ["internet-users", 200, true]]);
    expect(t.lines.map((l) => [l.from, l.to, l.mix, l.rate_cps])).toEqual([["rack1-web", "rack3-db", "sql-oltp", 20], ["internet-users", "rack2-web", "web-browse", 30]]);
  });

  it("Emulated, external groups and lines write the tfc profile only: te is byte-identical", () => {
    let d = doc();
    d = step(d, (x, m) => setExternalGroup(x, m, USERS));
    d = step(d, (x, m) => setEmulated(x, m, "rack1-web", 4));
    d = step(d, (x, m) => setEmulated(x, m, "rack3-db", 2));
    d = step(d, (x, m) => addLine(x, m, { id: null, from: "internet-users", to: "rack1-web", mix: "web-browse", rate_cps: 30, max_concurrent: null }));
    expect(d.files["te.yaml"]).toBe(FILES["te.yaml"]);
    expect(d.files["tap/default.yaml"]).toBe(FILES["tap/default.yaml"]);
    const text = d.files["tfc/default.yaml"]!;
    expect(text.startsWith(FILES["tfc/default.yaml"].trimEnd())).toBe(true); // the old lines kept as they were
    expect(text).toContain("  budget: { cps: 100, mbps: 60 }");
    expect(text).toContain("    - { group: internet-users, external: true, segment: internet, role: internet-user, count: 200 }");
    expect(text).toContain("    - { group: rack1-web, count: 4 }");
    expect(text).toContain("    - { from: internet-users, to: rack1-web, mix: web-browse, rate_cps: 30 }");
    const t = readTfc(text);
    expect(t.assignments).toHaveLength(2);
    // Edit and remove round-trip through the same blocks.
    d = step(d, (x, m) => setLine(x, m, 0, { rate_cps: 45, max_concurrent: 200 }));
    expect(readTfc(d.files["tfc/default.yaml"]!).lines[0]).toMatchObject({ rate_cps: 45, max_concurrent: 200 });
    d = step(d, (x, m) => setEmulated(x, m, "rack3-db", 0));
    expect(readTfc(d.files["tfc/default.yaml"]!).emulated.map((e) => e.group)).toEqual(["internet-users", "rack1-web"]);
    d = step(d, (x, m) => removeLine(x, m, 0));
    expect(readTfc(d.files["tfc/default.yaml"]!).lines).toEqual([]);
    expect(() => setBudget(d, readDoc(d), { cps: 0, mbps: 1 })).toThrow(/> 0/);
  });

  it("badges, E-W / N-S tags and the planned generators follow the lines' ends", () => {
    const m = readDoc(doc({ ...FILES, "tfc/default.yaml": GATE }));
    expect(groupBadge(m.tfc, "rack1-web")).toBe("client");
    expect(groupBadge(m.tfc, "rack2-web")).toBe("server");
    expect(groupBadge(m.tfc, "rack1-db")).toBeNull();
    expect(lineTag(m.tfc, m.tfc!.lines[0]!)).toBe("E-W");
    expect(lineTag(m.tfc, m.tfc!.lines[1]!)).toBe("N-S");
    expect(plannedGenerators(m)).toEqual(["gen-rack1-lan--rack3-lan", "gen-internet--rack2-lan"]);
    expect(trafficHealth(m)).toEqual({ cps: 50, budget: 100, mbps: 60, generators: 2, lines: 2 });
    expect(lineReasons(m, PERSONAS, 0)).toEqual([]);
  });

  it("a row says why it is refused: no emulated end, the wrong mix, two generators, over budget", () => {
    let d = doc();
    d = step(d, (x, m) => setExternalGroup(x, m, USERS));
    d = step(d, (x, m) => setEmulated(x, m, "rack1-web", 4));
    d = step(d, (x, m) => addLine(x, m, { id: null, from: "internet-users", to: "rack1-web", mix: "web-browse", rate_cps: 30, max_concurrent: null }));
    d = step(d, (x, m) => addLine(x, m, { id: null, from: "rack1-web", to: "rack3-db", mix: "sql-oltp", rate_cps: 80, max_concurrent: null }));
    const m = readDoc(d);
    const why = lineReasons(m, PERSONAS, 1);
    expect(why.map((r) => r.rule)).toEqual(["TGN-020", "PLAN", "TGN-040"]);
    expect(why[1]!.message).toBe("rack1-web would need two generators: line 'internet-users--rack1-web--web-browse' puts it on gen-internet--rack1-lan, this line on gen-rack1-lan--rack3-lan");
    expect(lineReasons(m, PERSONAS, 0).map((r) => r.rule)).toEqual(["TGN-040"]);
    const wrongMix = readDoc(step(d, (x, mo) => setLine(x, mo, 1, { mix: "web-browse" })));
    expect(lineReasons(wrongMix, PERSONAS, 1).map((r) => r.message)).toContain("web-browse emits to no db-tier");
    // The mix picker offers only mixes that emit to the server end's role.
    expect(mixesFor(PERSONAS, "db-tier").map((p) => p.id)).toEqual(["sql-oltp"]);
  });

  it("no traffic: a role binding becomes node bindings for the role's other hosts; te is untouched", () => {
    const d = doc();
    const m = readDoc(d);
    const web1 = groupInstances(m.te.nodes.find((n) => n.name === "rack1-web")!);
    expect(web1).toEqual(["rack1-web01", "rack1-web02"]);
    expect(hasNoTraffic(m, web1)).toBe(false);
    const after = step(d, (x, mo) => setNoTraffic(x, mo, web1));
    expect(after.files["te.yaml"]).toBe(FILES["te.yaml"]);
    const m2 = readDoc(after);
    expect(hasNoTraffic(m2, web1)).toBe(true);
    const t = readTfc(after.files["tfc/default.yaml"]!);
    expect(t.assignments.filter((a) => a.by === "role").map((a) => a.target)).toEqual(["db-tier"]);
    expect(t.assignments.filter((a) => a.by === "node").map((a) => `${a.target}=${a.persona}`)).toEqual([
      "rack2-web01=web-tier", "rack2-web02=web-tier", "rack3-web01=web-tier", "rack3-web02=web-tier"]);
    expect(hasNoTraffic(m2, ["rack2-web01"])).toBe(false);
  });
});

describe("the Emulated group tool (T11 decided D): an emulated-only group on any segment", () => {
  it("writes tfc only, makes the name unique, is drawn on its segment, and its lines are E-W", async () => {
    const { applyEmulatedDrop } = await import("../src/features/edit-canvas/add/drop");
    const { documentGraph } = await import("../src/features/edit-canvas/edit/document");
    let d = doc();
    const te0 = d.files["te.yaml"];
    d = step(d, (x, m) => setEmulated(x, m, "rack1-web", 4));
    d = step(d, (x, m) => setEmulated(x, m, "rack3-db", 2));
    d = step(d, (x, m) => setExternalGroup(x, m, USERS));
    const r = applyEmulatedDrop(d, readDoc(d), { segment: "rack1-lan", name: "rack1-web", role: "web-tier", count: 4 });
    expect(r.names).toEqual(["rack1-web-2"]); // a te node group's name is taken
    d = { ...d, files: r.files };
    expect(d.files["te.yaml"]).toBe(te0);
    expect(d.files["tfc/default.yaml"]).toContain("{ group: rack1-web-2, external: true, segment: rack1-lan, role: web-tier, count: 4 }");
    expect(() => applyEmulatedDrop(d, readDoc(d), { segment: "nope", name: "", role: "x", count: 1 })).toThrow(/no segment nope/);
    // The canvas's two-group answer to "rack1-web both serves Internet users and calls rack3-db".
    d = step(d, (x, m) => addLine(x, m, { from: "internet-users", to: "rack1-web", mix: "web-browse", rate_cps: 30 }));
    d = step(d, (x, m) => addLine(x, m, { from: "rack1-web-2", to: "rack3-db", mix: "sql-oltp", rate_cps: 20 }));
    const m = readDoc(d);
    expect(m.tfc!.lines.map((l) => lineTag(m.tfc, l))).toEqual(["N-S", "E-W"]);
    expect(plannedGenerators(m)).toEqual(["gen-internet--rack1-lan", "gen-rack1-lan--rack3-lan"]);
    m.tfc!.lines.forEach((_, i) => expect(lineReasons(m, PERSONAS, i)).toEqual([]));
    const g = documentGraph(m);
    expect(JSON.stringify(g)).toContain("rack1-web-2");
  });
});
