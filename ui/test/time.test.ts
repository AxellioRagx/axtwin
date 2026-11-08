/**
 * The console's one timestamp presentation (R4-7, 2026-11-08): local time with the zone, to the
 * minute; the exact UTC instant for a tooltip. Every date helper the pages use goes through it.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { formatWhen } from "../src/areas/shell/parts";
import { formatIso, formatTs } from "../src/model/sources";
import { formatInstant, formatInstantUtc } from "../src/model/time";

describe("one timestamp style", () => {
  let tz: string | undefined;
  beforeEach(() => {
    tz = process.env["TZ"];
    process.env["TZ"] = "America/Chicago";
  });
  afterEach(() => {
    if (tz === undefined) delete process.env["TZ"];
    else process.env["TZ"] = tz;
  });

  it("is local time with the zone abbreviation, from an ISO string or epoch seconds, through every helper", () => {
    const iso = "2026-10-06T19:00:00Z";
    const epoch = Date.parse(iso) / 1000;
    expect(formatInstant(iso)).toBe("2026-10-06 14:00 CDT");
    expect(formatInstant(epoch)).toBe("2026-10-06 14:00 CDT");
    // The list pages' helper, the Discovery pages' two: the same string for the same instant.
    expect(formatWhen(iso)).toBe("2026-10-06 14:00 CDT");
    expect(formatTs(epoch)).toBe("2026-10-06 14:00 CDT");
    expect(formatIso(iso)).toBe("2026-10-06 14:00 CDT");
    // Winter time names its own zone.
    expect(formatInstant("2026-01-15T12:30:36Z")).toBe("2026-01-15 06:30 CST");
    // The tooltip: the exact instant in UTC, to the second.
    expect(formatInstantUtc("2026-10-06T19:30:36Z")).toBe("2026-10-06 19:30:36 UTC");
    // Not a time: each helper's own fallback.
    expect(formatInstant(null)).toBe("—");
    expect(formatTs(null)).toBe("unknown");
    expect(formatIso("not a date")).toBe("not a date");
  });
});
