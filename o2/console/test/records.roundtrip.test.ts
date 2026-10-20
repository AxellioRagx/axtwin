// @vitest-environment node
/**
 * The S5 acceptance's round trip: each of the seven gestures produces exactly
 * the record `docs/design/14-operator-assertions.md` §3 specifies, VERIFIED by
 * assembling the save document, writing it to a file, and reading it back --
 * so the assertion is against the bytes a save persists, not only the object in
 * memory. This file does real filesystem work, so it opts into the **node**
 * environment (LES-260: under jsdom `import.meta.url` is not a `file://` URL),
 * and uses its OWN temp directory (LES-233: never glob a shared one).
 *
 * **What this proves and what it does not.** The client `PUT`s the JSON
 * `documentFrom` builds; the SERVER (`axrange/api/console_files.py`) transcodes
 * that JSON to `<id>.assertions.yaml` and owns the byte-level YAML assertion
 * (`tests/test_api_console.py`, S1). So the round trip here is JSON -> file ->
 * JSON, which is the client's half of the contract: the record survives
 * serialization unchanged and is exactly `14` §3's shape. The blocks that carry
 * `kind` (add) and those that do not (override/suppress) are asserted as such.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { buildRecord, type Gesture } from "../src/edit/records";
import { documentFrom, editReducer, initialEditState, type EditStoreState } from "../src/edit/store";

const GESTURES: Gesture[] = [
  { kind: "add-segment", cidr: "10.80.31.0/24", name: "seg-10-80-31", zoneType: "vlan", note: "the OT lab" },
  {
    kind: "add-host",
    key: "address:10.80.20.5",
    segment: "10.80.20.0/24",
    role: "domain-controller",
    listen: [{ port: 88, proto: "tcp" }, { port: 389, proto: "tcp" }],
    note: "the DC answers nothing on the wire",
  },
  { kind: "add-conduit", cidrA: "10.80.31.0/24", cidrB: "10.80.20.0/24", note: "the OT lab reaches the DC" },
  { kind: "change-role", key: "address:10.80.20.9", role: "file-server", note: "it answers SMB" },
  { kind: "override-image", key: "address:10.80.20.9", image: "debian-12", note: "pin the image" },
  { kind: "override-resources", key: "address:10.80.20.9", resources: { cpu: 2, ram: "4096" }, note: "size it up" },
  { kind: "suppress-host", key: "address:203.0.113.9", note: "out of scope: a cloud CDN edge" },
];

function stageAll(): EditStoreState {
  let state = initialEditState("corpapcn-golden");
  for (const g of GESTURES) {
    const result = buildRecord(g);
    if (!result.ok) throw new Error(`gesture ${g.kind} refused: ${result.error}`);
    state = editReducer(state, { type: "stage", block: result.staged.block, record: result.staged.record });
  }
  return state;
}

describe("gesture -> record -> save document -> written file -> read back", () => {
  let dir: string;
  let readBack: {
    add: Array<Record<string, unknown>>;
    override: Array<Record<string, unknown>>;
    suppress: Array<Record<string, unknown>>;
  };

  beforeAll(() => {
    dir = mkdtempSync(join(tmpdir(), "axr-console-s5-"));
    const document = documentFrom(stageAll(), {
      schema: "axr-assertions/1",
      imprint: "corpapcn-golden",
      as_of: "2026-09-10T00:00:00Z",
      source: "operator: tester",
    });
    // Write what the client PUTs. The server transcodes JSON -> YAML; the
    // Python suite owns the YAML byte assertion (S1).
    const path = join(dir, "corpapcn-golden.assertions.json");
    writeFileSync(path, JSON.stringify(document, null, 2), "utf-8");
    readBack = JSON.parse(readFileSync(path, "utf-8")) as typeof readBack;
  });

  afterAll(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("carries the document's own metadata", () => {
    const doc = readBack as unknown as Record<string, unknown>;
    expect(doc["schema"]).toBe("axr-assertions/1");
    expect(doc["imprint"]).toBe("corpapcn-golden");
    expect(doc["as_of"]).toBe("2026-09-10T00:00:00Z");
    expect(doc["source"]).toBe("operator: tester");
  });

  it("the add block has the segment, host and conduit records, each with kind", () => {
    expect(readBack.add).toEqual([
      { kind: "segment", key: "10.80.31.0/24", name: "seg-10-80-31", zone_type: "vlan", note: "the OT lab" },
      {
        kind: "host",
        key: "address:10.80.20.5",
        segment: "10.80.20.0/24",
        role: "domain-controller",
        listen: [{ port: 88, proto: "tcp" }, { port: 389, proto: "tcp" }],
        note: "the DC answers nothing on the wire",
      },
      // Conduit halves are sorted (server-canonical).
      { kind: "conduit", key: "10.80.20.0/24|10.80.31.0/24", note: "the OT lab reaches the DC" },
    ]);
  });

  it("the override block has role/image/resources, NO kind (the block is the kind)", () => {
    expect(readBack.override).toEqual([
      { key: "address:10.80.20.9", field: "role", value: "file-server", note: "it answers SMB" },
      { key: "address:10.80.20.9", field: "image", value: "debian-12", note: "pin the image" },
      { key: "address:10.80.20.9", field: "resources", value: { cpu: 2, ram: "4096" }, note: "size it up" },
    ]);
    for (const rec of readBack.override) expect("kind" in rec).toBe(false);
  });

  it("the suppress block has { key, note } only", () => {
    expect(readBack.suppress).toEqual([{ key: "address:203.0.113.9", note: "out of scope: a cloud CDN edge" }]);
    expect("kind" in (readBack.suppress[0] as object)).toBe(false);
  });

  it("a no-op re-save writes a byte-identical document", () => {
    // The store rebuilds the document deterministically from the same entries,
    // so re-saving without a change produces the same bytes -- which is what
    // lets the server keep its content-hash etag (no invented conflict).
    const state = stageAll();
    const base = {
      schema: "axr-assertions/1" as const,
      imprint: "corpapcn-golden",
      as_of: "2026-09-10T00:00:00Z",
      source: "operator: tester",
    };
    const a = JSON.stringify(documentFrom(state, base));
    const b = JSON.stringify(documentFrom(state, base));
    expect(a).toBe(b);
  });
});
