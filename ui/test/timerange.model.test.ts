import { describe, expect, it } from "vitest";

import type { DiscoveryRun } from "../src/api/types";
import {
  burstChoices,
  clampToSpan,
  estimateCost,
  fitSubbuckets,
  formatSpan,
  gapsIn,
  rangeBody,
  recentRanges,
  resolveRange,
  spanOf,
} from "../src/model/timeRange";
import { SPAN } from "./helpers/fakeApi";

const probe = { reachable: true, error: null, ...SPAN, vantage: null, extra: { chunks: 69, packets: 1_000_000, bytes: 3_000_000 }, via: null };
const span = spanOf(probe)!;

describe("the run form's time range", () => {
  it("quick ranges count back from the END of the data, not from now", () => {
    expect(span).toEqual({ first: SPAN.first_ts, end: SPAN.last_ts + 1 });
    expect(resolveRange({ kind: "relative", seconds: 6 * 3600 }, span)).toEqual({ from: SPAN.last_ts + 1 - 6 * 3600, to: SPAN.last_ts + 1 });
    expect(resolveRange({ kind: "relative", seconds: null }, span)).toEqual({ from: SPAN.first_ts, to: SPAN.last_ts + 1 });
    // Longer than the data held: starts at the span's start.
    expect(resolveRange({ kind: "relative", seconds: 365 * 86400 }, span)?.from).toBe(SPAN.first_ts);
    expect(resolveRange({ kind: "relative", seconds: 3600 }, null)).toBeNull();
  });

  it("a relative range is sent as days, an absolute one as bounds", () => {
    expect(rangeBody({ kind: "relative", seconds: 6 * 3600 }, null)).toEqual({ days: 0.25 });
    expect(rangeBody({ kind: "relative", seconds: null }, span)).toEqual({ days: (span.end - span.first) / 86400 });
    expect(rangeBody({ kind: "relative", seconds: null }, null)).toBeNull();
    expect(rangeBody({ kind: "absolute", from: 10, to: 20 }, span)).toEqual({ ts_from: 10, ts_to: 20 });
  });

  it("absolute bounds are kept inside the span", () => {
    expect(clampToSpan(SPAN.first_ts - 100, SPAN.last_ts + 500, span)).toEqual({ from: SPAN.first_ts, to: SPAN.last_ts + 1, clamped: true });
    expect(clampToSpan(SPAN.first_ts + 1, SPAN.first_ts + 2, span).clamped).toBe(false);
    expect(clampToSpan(1, 2, null)).toEqual({ from: 1, to: 2, clamped: false });
  });

  it("recently used: this source's earlier ranges, newest first, each once", () => {
    const r = (id: string, created_at: string, ts_from: number | null, ts_to: number | null, source_id = "src-1") =>
      ({ id, created_at, ts_from, ts_to, source_id }) as DiscoveryRun;
    const runs = [r("a", "2026-11-01", 1, 2), r("b", "2026-11-03", 3, 4), r("c", "2026-11-02", 1, 2), r("d", "2026-11-04", 5, 6, "src-x"), r("e", "2026-11-05", null, null)];
    expect(recentRanges(runs, "src-1")).toEqual([{ from: 3, to: 4, runId: "b" }, { from: 1, to: 2, runId: "c" }]);
  });

  it("estimates cost by scaling the census to the selected share of the span", () => {
    const half = { from: span.first, to: span.first + (span.end - span.first) / 2 };
    expect(estimateCost(half, span, probe.extra)).toEqual({ chunks: 35, packets: 500_000, bytes: 1_500_000 });
    expect(estimateCost(half, span, {})).toBeNull();
    expect(estimateCost(null, span, probe.extra)).toBeNull();
  });

  it("warns only about gaps the census reports", () => {
    expect(gapsIn({ from: 0, to: 100 }, { gaps: [[10, 20], [200, 300], "x"] })).toEqual([[10, 20]]);
    expect(gapsIn({ from: 0, to: 100 }, {})).toEqual([]);
  });

  it("burst resolution only offers slices that divide the interval evenly", () => {
    expect(burstChoices(3600)).toContain(12);
    expect(burstChoices(900).every((n) => 900 % n === 0)).toBe(true);
    expect(burstChoices(900)).not.toContain(8);
    expect(fitSubbuckets(3600, 12)).toBe(12);
    expect(fitSubbuckets(900, 8)).toBe(3); // 5 min slices
    expect([formatSpan(3600), formatSpan(300), formatSpan(900), formatSpan(14400), formatSpan(7 * 86400)]).toEqual(["1 h", "5 min", "15 min", "4 h", "7 d"]);
  });
});
