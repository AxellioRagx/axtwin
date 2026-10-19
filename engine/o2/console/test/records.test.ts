/**
 * The gesture -> record mapping (`src/edit/records.ts`), the heart of S5.
 *
 * Each of the seven gestures of `docs/design/14-operator-assertions.md` §4
 * must produce EXACTLY the record `14` §3 specifies -- asserted here against a
 * literal, so a drift in a field name or a missing kind fails loudly. The
 * round-trip-through-a-file half of the acceptance is `records.roundtrip.test.ts`;
 * this file is the pure shape + the validation pre-flight + the change-list tag.
 */
import { describe, expect, it } from "vitest";

import {
  buildRecord,
  crossRecordConflict,
  deployConsequence,
  strictCidr,
  type BuildResult,
  type StagedRecord,
} from "../src/edit/records";
import type { AssertionBlock } from "../src/api/types";

function record(result: BuildResult): { block: string; rec: Record<string, unknown> } {
  if (!result.ok) throw new Error(`expected ok, got: ${result.error}`);
  return { block: result.staged.block, rec: result.staged.record as Record<string, unknown> };
}

describe("the seven gestures produce exactly the record 14 §3 specifies", () => {
  it("1. add a segment -> add { kind: segment, key, name, zone_type, note }", () => {
    const { block, rec } = record(
      buildRecord({
        kind: "add-segment",
        cidr: "10.80.31.0/24",
        name: "seg-10-80-31",
        zoneType: "vlan",
        note: "pruned by the cast; it is the OT lab",
      }),
    );
    expect(block).toBe("add");
    expect(rec).toEqual({
      kind: "segment",
      key: "10.80.31.0/24",
      name: "seg-10-80-31",
      zone_type: "vlan",
      note: "pruned by the cast; it is the OT lab",
    });
  });

  it("2. add a host -> add { kind: host, key, segment, role, listen, note }", () => {
    const { block, rec } = record(
      buildRecord({
        kind: "add-host",
        key: "address:10.80.20.5",
        segment: "10.80.20.0/24",
        role: "domain-controller",
        listen: [
          { port: 88, proto: "tcp" },
          { port: 389, proto: "tcp" },
        ],
        note: "the DC; it answers nothing on the wire",
      }),
    );
    expect(block).toBe("add");
    expect(rec).toEqual({
      kind: "host",
      key: "address:10.80.20.5",
      segment: "10.80.20.0/24",
      role: "domain-controller",
      listen: [
        { port: 88, proto: "tcp" },
        { port: 389, proto: "tcp" },
      ],
      note: "the DC; it answers nothing on the wire",
    });
  });

  it("2b. a host add with no role/listen omits them (they are optional)", () => {
    const { rec } = record(
      buildRecord({ kind: "add-host", key: "hostname:axdc01", segment: "10.80.20.0/24", note: "n" }),
    );
    expect(rec).toEqual({ kind: "host", key: "hostname:axdc01", segment: "10.80.20.0/24", note: "n" });
    expect("role" in rec).toBe(false);
    expect("listen" in rec).toBe(false);
  });

  it("3. change a role -> override { key, field: role, value, note }", () => {
    const { block, rec } = record(
      buildRecord({
        kind: "change-role",
        key: "address:10.80.20.5",
        role: "domain-controller",
        note: "inferred dns-resolver from port 53; it is the DC",
      }),
    );
    expect(block).toBe("override");
    // No `kind` on an override record: the block is the kind, and `14` §3's
    // own file shape carries none.
    expect(rec).toEqual({
      key: "address:10.80.20.5",
      field: "role",
      value: "domain-controller",
      note: "inferred dns-resolver from port 53; it is the DC",
    });
  });

  it("4. suppress a host -> suppress { key, note }", () => {
    const { block, rec } = record(
      buildRecord({ kind: "suppress-host", key: "address:203.0.113.9", note: "out of scope: a cloud CDN edge" }),
    );
    expect(block).toBe("suppress");
    expect(rec).toEqual({ key: "address:203.0.113.9", note: "out of scope: a cloud CDN edge" });
  });

  it("5. override image -> override { key, field: image, value, note }", () => {
    const { block, rec } = record(
      buildRecord({ kind: "override-image", key: "address:10.80.20.5", image: "debian-12", note: "n" }),
    );
    expect(block).toBe("override");
    expect(rec).toEqual({ key: "address:10.80.20.5", field: "image", value: "debian-12", note: "n" });
  });

  it("6. override sizing -> override { key, field: resources, value, note }", () => {
    const { block, rec } = record(
      buildRecord({
        kind: "override-resources",
        key: "address:10.80.20.5",
        resources: { cpu: 2, ram: "4096" },
        note: "it is a DC, size it up",
      }),
    );
    expect(block).toBe("override");
    expect(rec).toEqual({
      key: "address:10.80.20.5",
      field: "resources",
      value: { cpu: 2, ram: "4096" },
      note: "it is a DC, size it up",
    });
  });

  it("7. add a conduit -> add { kind: conduit, key: '<a>|<b>', note } (halves SORTED)", () => {
    // Authored B-then-A; the canonical key is sorted, matching the compiler's
    // `_canonical_conduit_key`.
    const { block, rec } = record(
      buildRecord({
        kind: "add-conduit",
        cidrA: "10.80.31.0/24",
        cidrB: "10.80.20.0/24",
        note: "the OT lab reaches the DC",
      }),
    );
    expect(block).toBe("add");
    expect(rec).toEqual({
      kind: "conduit",
      key: "10.80.20.0/24|10.80.31.0/24",
      note: "the OT lab reaches the DC",
    });
  });
});

describe("every record requires a non-empty note (14 §3)", () => {
  const gestures = [
    { kind: "add-segment", cidr: "10.80.31.0/24", name: "s", zoneType: "vlan" },
    { kind: "add-host", key: "address:10.0.0.1", segment: "10.0.0.0/24" },
    { kind: "change-role", key: "address:10.0.0.1", role: "web-tier" },
    { kind: "suppress-host", key: "address:10.0.0.1" },
    { kind: "override-image", key: "address:10.0.0.1", image: "debian-12" },
    { kind: "override-resources", key: "address:10.0.0.1", resources: { cpu: 1 } },
    { kind: "add-conduit", cidrA: "10.0.0.0/24", cidrB: "10.0.1.0/24" },
  ] as const;

  for (const g of gestures) {
    for (const note of ["", "   "]) {
      it(`${g.kind} with note ${JSON.stringify(note)} is refused`, () => {
        const result = buildRecord({ ...(g as object), note } as Parameters<typeof buildRecord>[0]);
        expect(result.ok).toBe(false);
        if (!result.ok) expect(result.error).toMatch(/note/i);
      });
    }
  }
});

describe("the pre-flight mirrors the server's refusals (no stricter)", () => {
  it("refuses a segment key that is not a network address (host bits set)", () => {
    const r = buildRecord({ kind: "add-segment", cidr: "10.80.31.5/24", name: "s", note: "n" });
    expect(r.ok).toBe(false);
  });

  it("refuses a segment name with whitespace", () => {
    const r = buildRecord({ kind: "add-segment", cidr: "10.80.31.0/24", name: "seg 31", note: "n" });
    expect(r.ok).toBe(false);
  });

  it("refuses a host add whose segment is not a CIDR", () => {
    const r = buildRecord({ kind: "add-host", key: "address:10.0.0.5", segment: "not-a-cidr", note: "n" });
    expect(r.ok).toBe(false);
  });

  it("refuses a suppress keyed on a CIDR -- suppress takes host keys only", () => {
    const r = buildRecord({ kind: "suppress-host", key: "10.80.20.0/24", note: "n" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/host key/i);
  });

  it("refuses a resources override with an unknown key", () => {
    const r = buildRecord({
      kind: "override-resources",
      key: "address:10.0.0.1",
      resources: { gpu: 1 } as unknown as { cpu?: number },
      note: "n",
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toMatch(/cpu|disk|ram/);
  });

  it("refuses an empty resources override", () => {
    const r = buildRecord({ kind: "override-resources", key: "address:10.0.0.1", resources: {}, note: "n" });
    expect(r.ok).toBe(false);
  });

  it("refuses a conduit joining a segment to itself", () => {
    const r = buildRecord({ kind: "add-conduit", cidrA: "10.0.0.0/24", cidrB: "10.0.0.0/24", note: "n" });
    expect(r.ok).toBe(false);
  });

  it("refuses a host add with a bare (prefix-less) key", () => {
    const r = buildRecord({ kind: "add-host", key: "axdc01", segment: "10.0.0.0/24", note: "n" });
    expect(r.ok).toBe(false);
  });

  it("ACCEPTS a non-IP address key (the server warns, never refuses)", () => {
    // `address:not-an-ip` joins against nothing, but the reader of record
    // accepts it; refusing here would be stricter than the server.
    const r = buildRecord({ kind: "add-host", key: "address:not-an-ip", segment: "10.0.0.0/24", note: "n" });
    expect(r.ok).toBe(true);
  });
});

describe("strictCidr (the network-form check)", () => {
  it("accepts a network address", () => {
    expect(strictCidr("10.80.31.0/24")).toBe("10.80.31.0/24");
    expect(strictCidr("  10.80.31.0/24  ")).toBe("10.80.31.0/24");
    expect(strictCidr("10.0.0.0/8")).toBe("10.0.0.0/8");
    expect(strictCidr("192.168.1.1/32")).toBe("192.168.1.1/32");
  });
  it("rejects a host address with a shorter prefix", () => {
    expect(strictCidr("10.80.31.5/24")).toBeNull();
    expect(strictCidr("192.168.1.1/24")).toBeNull();
  });
  it("rejects a non-CIDR", () => {
    expect(strictCidr("10.80.31.0")).toBeNull();
    expect(strictCidr("nope")).toBeNull();
  });
  it("rejects leading-zero octets, which the server's ip_network refuses (N1)", () => {
    // Without the guard these parse loosely and are refused only later, at
    // save, as a generic 400; refuse them inline instead.
    expect(strictCidr("10.080.31.0/24")).toBeNull();
    expect(strictCidr("010.80.31.0/24")).toBeNull();
    // A single-zero octet is NOT a leading zero and stays valid.
    expect(strictCidr("10.0.0.0/8")).toBe("10.0.0.0/8");
  });
});

describe("crossRecordConflict (the two cross-record refusals 14 §3 names)", () => {
  const entry = (block: AssertionBlock, record: Record<string, unknown>) => ({ block, record });
  const staged = (block: AssertionBlock, record: Record<string, unknown>): StagedRecord =>
    ({ block, record }) as StagedRecord;

  it("refuses a key in both add and suppress", () => {
    const existing = [entry("add", { kind: "host", key: "address:10.0.0.5", segment: "10.0.0.0/24", note: "n" })];
    const c = crossRecordConflict(existing, staged("suppress", { key: "address:10.0.0.5", note: "n" }));
    expect(c).toMatch(/add and suppress/);
    // ...and the reverse direction.
    const rev = crossRecordConflict(
      [entry("suppress", { key: "address:10.0.0.5", note: "n" })],
      staged("add", { kind: "host", key: "address:10.0.0.5", segment: "10.0.0.0/24", note: "n" }),
    );
    expect(rev).toMatch(/add and suppress/);
  });

  it("refuses a (key, field) in both an add's fields and an override", () => {
    const addWithRole = entry("add", { kind: "host", key: "address:10.0.0.5", role: "web-tier", note: "n" });
    const c = crossRecordConflict([addWithRole], staged("override", { key: "address:10.0.0.5", field: "role", value: "db-tier", note: "n" }));
    expect(c).toMatch(/both add and override/);
    // The reverse: staging the add when the override already exists.
    const rev = crossRecordConflict(
      [entry("override", { key: "address:10.0.0.5", field: "role", value: "db-tier", note: "n" })],
      staged("add", { kind: "host", key: "address:10.0.0.5", role: "web-tier", note: "n" }),
    );
    expect(rev).toMatch(/already overridden/);
  });

  it("allows an override whose field the add does NOT carry", () => {
    // An add-host carrying only `segment` does not clash with an image override.
    const addNoImage = entry("add", { kind: "host", key: "address:10.0.0.5", segment: "10.0.0.0/24", note: "n" });
    expect(
      crossRecordConflict([addNoImage], staged("override", { key: "address:10.0.0.5", field: "image", value: "debian-12", note: "n" })),
    ).toBeNull();
  });

  it("allows two overrides on one host for different fields", () => {
    const role = entry("override", { key: "address:10.0.0.5", field: "role", value: "web-tier", note: "n" });
    expect(
      crossRecordConflict([role], staged("override", { key: "address:10.0.0.5", field: "image", value: "debian-12", note: "n" })),
    ).toBeNull();
  });
});

describe("the change-list tag is derivable from the block alone (15 §8)", () => {
  it("add and suppress flow through te deploy --update", () => {
    expect(deployConsequence("add")).toBe("te deploy --update");
    expect(deployConsequence("suppress")).toBe("te deploy --update");
  });
  it("an override is a fresh deploy", () => {
    expect(deployConsequence("override")).toBe("fresh deploy");
  });
});
