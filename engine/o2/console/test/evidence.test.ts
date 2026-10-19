import { describe, expect, it } from "vitest";

import {
  EVIDENCE_CLASSES,
  countByClass,
  SATURATION_FLOOR,
  ghostReasons,
  isAuthorable,
  isEvidenceClass,
  isGhost,
  outranks,
  precedenceOf,
  saturationFor,
} from "../src/model/evidence";

describe("the six evidence classes", () => {
  it("is exactly the vocabulary of `14` §2, in precedence order", () => {
    expect([...EVIDENCE_CLASSES]).toEqual([
      "asserted",
      "imported",
      "observed",
      "referenced",
      "imputed",
      "compiler",
    ]);
  });

  it("orders every pair by that list", () => {
    for (let i = 0; i < EVIDENCE_CLASSES.length; i += 1) {
      for (let j = i + 1; j < EVIDENCE_CLASSES.length; j += 1) {
        const higher = EVIDENCE_CLASSES[i]!;
        const lower = EVIDENCE_CLASSES[j]!;
        expect(outranks(higher, lower), `${higher} > ${lower}`).toBe(true);
        expect(outranks(lower, higher), `${lower} < ${higher}`).toBe(false);
      }
    }
  });

  it("puts an assertion above an observation, and compiler last", () => {
    expect(outranks("asserted", "observed")).toBe(true);
    expect(outranks("observed", "imputed")).toBe(true);
    expect(outranks("imputed", "compiler")).toBe(true);
    expect(precedenceOf("asserted")).toBe(0);
    expect(precedenceOf("compiler")).toBe(EVIDENCE_CLASSES.length - 1);
  });

  it("treats an unknown class as unknown, never as a familiar one", () => {
    expect(isEvidenceClass("defaulted")).toBe(false); // the pre-rename spelling
    expect(precedenceOf("defaulted")).toBeNull();
    expect(outranks("defaulted", "compiler")).toBe(false);
    // ... but a known class still beats an unknown one, so a real fact wins.
    expect(outranks("compiler", "defaulted")).toBe(true);
  });

  it("NEVER offers `compiler` as an authorable class", () => {
    // `compiler` is the sidecar's word for "a compiler choice, not a
    // measurement". Authoring one would forge the compiler's signature.
    expect(isAuthorable("compiler")).toBe(false);
    for (const cls of EVIDENCE_CLASSES) {
      expect(isAuthorable(cls), cls).toBe(cls === "asserted");
    }
  });
});

describe("confidence -> saturation", () => {
  it("floors a known-but-low confidence so it stays visible", () => {
    expect(saturationFor(0)).toBe(SATURATION_FLOOR);
    expect(saturationFor(1)).toBe(1);
    expect(saturationFor(0.5)).toBeGreaterThan(SATURATION_FLOOR);
  });

  it("renders an ABSENT confidence at full saturation, not at zero", () => {
    // Absent is not zero: fading it would state a low confidence nobody
    // measured. A canonical `compiler` row has none by definition.
    expect(saturationFor(null)).toBe(1);
    expect(saturationFor(undefined)).toBe(1);
  });

  it("clamps out-of-range input rather than producing an invalid alpha", () => {
    expect(saturationFor(-3)).toBe(SATURATION_FLOOR);
    expect(saturationFor(42)).toBe(1);
  });
});

describe("the ghost rule", () => {
  it("names every reason, most actionable first", () => {
    expect(
      ghostReasons({ occupancy: 0.2, threshold: 0.8, suppressed: true }),
    ).toEqual(["suppressed", "below-threshold"]);
    expect(ghostReasons({ occupancy: 0.2, threshold: 0.8 })).toEqual([
      "below-threshold",
    ]);
    expect(ghostReasons({ occupancy: 0.9, threshold: 0.8 })).toEqual([]);
    expect(isGhost({ occupancy: 0.9, threshold: 0.8 })).toBe(false);
  });

  it("does not call an entity with NO occupancy a ghost", () => {
    // An absent occupancy is not a low one -- a bundle that predates the
    // observation block publishes none at all.
    expect(ghostReasons({ occupancy: null, threshold: 0.8 })).toEqual([]);
    expect(ghostReasons({ threshold: 0.8 })).toEqual([]);
  });

  it("carries the other three reasons through", () => {
    expect(
      ghostReasons({ threshold: 0.8, droppedByCast: true, referencedUnseen: true }),
    ).toEqual(["dropped-by-cast", "referenced-unseen"]);
  });
});

describe("counting a population by class", () => {
  it("zero-fills every known class, in precedence order", () => {
    const counts = countByClass([
      { evidence_class: "observed" },
      { evidence_class: "observed" },
      { evidence_class: "compiler" },
    ]);
    expect(counts.known.map((c) => c.evidenceClass)).toEqual([
      ...EVIDENCE_CLASSES,
    ]);
    expect(counts.known.map((c) => c.count)).toEqual([0, 0, 2, 0, 0, 1]);
    expect(counts.total).toBe(3);
    expect(counts.unknown).toEqual([]);
  });

  it("reports an unrecognized class instead of dropping or folding it", () => {
    // A producer that stamps a class this build does not know is publishing
    // information; renaming it to something familiar would be a fabrication,
    // and dropping it would make the counts disagree with the row list.
    const counts = countByClass([
      { evidence_class: "observed" },
      { evidence_class: "defaulted" }, // the pre-rename spelling of `compiler`
      { evidence_class: "vibes" },
      { evidence_class: "defaulted" },
    ]);
    expect(counts.unknown).toEqual([
      { evidenceClass: "defaulted", count: 2 },
      { evidenceClass: "vibes", count: 1 },
    ]);
    expect(counts.known.find((c) => c.evidenceClass === "compiler")?.count).toBe(0);
    expect(counts.total).toBe(4);
  });

  it("counts an empty population as zeros, not as an absence", () => {
    const counts = countByClass([]);
    expect(counts.total).toBe(0);
    expect(counts.known.every((c) => c.count === 0)).toBe(true);
  });
});

