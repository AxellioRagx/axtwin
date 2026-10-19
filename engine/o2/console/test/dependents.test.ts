/**
 * The pure rules behind the change list's "remove with dependents" and orphan
 * flags (`src/edit/dependents.ts`, the fix rounds' SF2 and its correction).
 *
 * Restated for the correction (LES-299): a segment's HOMES are the ones the
 * compiler resolves a record against -- the observed segments and this file's
 * adds (aximprint `synth/compiler.py` `known_cidrs`) -- so an add of an OBSERVED
 * segment has no dependents, and the installed cast's roster is not a home.
 */
import { describe, expect, it } from "vitest";

import {
  dependentsOf,
  orphanReasons,
  removalConsequences,
  type DependentEntryInput,
} from "../src/edit/dependents";

const seg = (key: string): DependentEntryInput => ({ id: `add:${key}`, block: "add", record: { kind: "segment", key } });
const host = (key: string, segment: string): DependentEntryInput => ({ id: `add:${key}`, block: "add", record: { kind: "host", key, segment } });
const conduit = (key: string): DependentEntryInput => ({ id: `add:${key}`, block: "add", record: { kind: "conduit", key } });
const override = (key: string): DependentEntryInput => ({ id: `override:${key}:role`, block: "override", record: { key } });

const OBSERVED = new Set(["10.80.20.0/24", "10.1.10.0/24"]);

describe("dependentsOf -- the records an added segment is the ONLY home of", () => {
  const entries = [
    seg("10.80.31.0/24"),
    host("address:10.80.31.10", "10.80.31.0/24"),
    host("address:10.80.20.5", "10.80.20.0/24"),
    conduit("10.80.20.0/24|10.80.31.0/24"),
    conduit("10.1.10.0/24|10.80.20.0/24"),
    override("address:10.80.31.10"),
  ];

  it("is its hosts and the conduits touching it, in store order -- when nothing else is their home", () => {
    expect(dependentsOf(entries, entries[0]!, OBSERVED).map((e) => e.id)).toEqual([
      "add:address:10.80.31.10",
      "add:10.80.20.0/24|10.80.31.0/24",
    ]);
  });

  it("is EMPTY for an add of an OBSERVED segment -- a host on it is valid without the add", () => {
    // The reviewer's case: `add: segment 10.80.20.0/24` (a zone assertion) and a
    // host on it. The compiler resolves the host against the observed segment,
    // so the add strands nothing and goes alone.
    const onObserved = [seg("10.80.20.0/24"), host("address:10.80.20.99", "10.80.20.0/24"), conduit("10.1.10.0/24|10.80.20.0/24")];
    expect(dependentsOf(onObserved, onObserved[0]!, OBSERVED)).toEqual([]);
  });

  it("is EMPTY when another record adds the same network -- it stays a home", () => {
    const twice: DependentEntryInput[] = [
      { id: "add:10.80.31.0/24", block: "add", record: { kind: "segment", key: "10.80.31.0/24" } },
      { id: "add:10.80.31.0/24#2", block: "add", record: { kind: "segment", key: " 10.80.31.0/24 " } },
      host("address:10.80.31.10", "10.80.31.0/24"),
    ];
    expect(dependentsOf(twice, twice[0]!, OBSERVED)).toEqual([]);
  });

  it("with the observed segments UNKNOWN, lists them conservatively", () => {
    expect(dependentsOf(entries, entries[0]!, null).map((e) => e.id)).toEqual([
      "add:address:10.80.31.10",
      "add:10.80.20.0/24|10.80.31.0/24",
    ]);
  });

  it("is empty for any entry that is not an add: segment", () => {
    for (const e of entries.slice(1)) expect(dependentsOf(entries, e, OBSERVED), e.id).toEqual([]);
  });

  it("matches a conduit HALF exactly -- 10.80.3.0/24 is not a dependent of 10.80.31.0/24's string prefix", () => {
    const near = [seg("10.80.3.0/24"), conduit("10.80.20.0/24|10.80.31.0/24")];
    expect(dependentsOf(near, near[0]!, OBSERVED)).toEqual([]);
  });
});

describe("removalConsequences -- what the records would meet left without the segment, in the compiler's own terms", () => {
  it("a host FAILS the apply; a conduit is reported ineffective and dropped -- never 'the apply would fail'", () => {
    const lines = removalConsequences([host("address:10.80.31.10", "10.80.31.0/24"), conduit("10.80.20.0/24|10.80.31.0/24")], true);
    expect(lines).toEqual([
      "left without it, the host would fail the apply — the compiler refuses a host whose segment is neither observed nor added by this file",
      "left without it, the conduit would be reported ineffective and dropped — the compiler does not emit a conduit to a segment the twin does not carry",
    ]);
    expect(removalConsequences([conduit("a|b"), conduit("a|c")], true)).toEqual([
      "left without it, the 2 conduits would be reported ineffective and dropped — the compiler does not emit a conduit to a segment the twin does not carry",
    ]);
  });

  it("with the observed segments unknown, says 'could' and why", () => {
    const lines = removalConsequences([host("address:10.80.31.10", "10.80.31.0/24")], false);
    expect(lines[0]).toMatch(/^left without it, the host could fail the apply/);
    expect(lines[1]).toMatch(/observed segments are not known here/);
    expect(removalConsequences([], false)).toEqual([]);
  });
});

describe("orphanReasons -- a record whose segment has no home", () => {
  it("flags a host and a conduit half that neither an observed segment nor an add of this file holds", () => {
    const entries = [
      seg("10.80.31.0/24"),
      host("address:10.80.31.10", "10.80.31.0/24"), // added
      host("address:10.80.20.5", "10.80.20.0/24"), // observed
      host("address:10.200.9.5", "10.200.9.0/24"), // a DEPLOYED roster CIDR: no home to the compiler
      host("address:10.80.99.5", "10.80.99.0/24"), // nowhere
      conduit("10.80.20.0/24|10.80.99.0/24"), // one half nowhere
    ];
    // Restated (LES-299): the roster is no longer a home, so the host on
    // 10.200.9.0/24 -- which the compiler refuses -- is flagged too.
    const reasons = orphanReasons(entries, { observed: OBSERVED });
    expect([...reasons.keys()]).toEqual([
      "add:address:10.200.9.5",
      "add:address:10.80.99.5",
      "add:10.80.20.0/24|10.80.99.0/24",
    ]);
    expect(reasons.get("add:address:10.80.99.5")).toBe(
      "orphaned: 10.80.99.0/24 is neither an observed segment nor one this file adds — the compiler refuses a host on it",
    );
    expect(reasons.get("add:10.80.20.0/24|10.80.99.0/24")).toBe(
      "orphaned: 10.80.99.0/24 is neither an observed segment nor one this file adds — the compiler reports the conduit ineffective and drops it",
    );
  });

  it("flags NOTHING while the observed segments are unknown -- a guess is not a finding", () => {
    // Restated (LES-299): with the roster no longer a home, "unknown" is exactly
    // "no observed set" -- the topology not loaded, or a bundle with no export.
    const entries = [host("address:10.80.99.5", "10.80.99.0/24")];
    expect(orphanReasons(entries, { observed: null }).size).toBe(0);
    // A KNOWN observed set, even an empty one, decides.
    expect(orphanReasons(entries, { observed: new Set() }).size).toBe(1);
  });
});
