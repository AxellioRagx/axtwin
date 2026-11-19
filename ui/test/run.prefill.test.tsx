import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DiscoveryRun, Imprint } from "../src/api/types";
import { AXQUERY, SPAN, fakeApi, tunnelSource } from "./helpers/fakeApi";
import { renderAt } from "./helpers/render";

function pastRun(over: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "disc-8d560f14", status: "completed", plugin: "axquery", name: "corp-apcn-1h", imprint_id: "corp-apcn-1h",
    stages_done: ["ingest", "bake"], stages: ["ingest", "bake"],
    pit_id: "pit-1", error: null, created_at: "2026-11-19T10:00:00Z", finished_at: "2026-11-19T10:05:00Z",
    source_id: "src-1", started_at: "2026-11-19T10:00:01Z", ts_from: 1791311437, ts_to: 1791315037,
    via: null, progress_ts: null, progress_at: null, cancel_requested_at: null,
    options: { network: "corp-from-run", vantage: "axellio-hq/rdr1:12", window: 900 }, ...over,
  };
}

const PROBE = {
  source_id: "src-1", probed_at: "2026-10-27T16:00:00Z", reachable: true, error: null, ...SPAN, vantage: null,
  extra: { chunks: 30, packets: 2_592_000_000, bytes: 2_592_000_000_000 }, via: null,
};
const END = SPAN.last_ts + 1;
const hint = async () => (await screen.findByTestId("run-prefill-hint")).textContent;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;
const lastRunBody = (state: { writes: Array<{ op: string; body?: unknown }> }) =>
  state.writes.filter((w) => w.op === "run").at(-1)!.body as Record<string, unknown>;

describe("the run form pre-fills from the source's last run, then its legacy defaults, then built-ins", () => {
  it("fills from the last run and names it; network and vantage are not carried", async () => {
    const { api, state } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource()], runs: [pastRun()] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Filled from run disc-8d560f14, the last one from this source."));
    const link = within(screen.getByTestId("run-prefill-hint")).getByRole("link", { name: "disc-8d560f14" });
    expect(link.getAttribute("href")).toBe("/discovery/runs/disc-8d560f14");
    expect(screen.getByRole("button", { name: "Last 1 h of data" }).getAttribute("aria-pressed")).toBe("true");
    expect(select("Measurement interval").value).toBe("900");
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.at(-1)?.op).toBe("run"));
    const body = lastRunBody(state);
    expect(body).toMatchObject({ days: 1 / 24, window: 900 });
    expect(body).not.toHaveProperty("network");
    expect(body).not.toHaveProperty("vantage");
  });

  it("falls back to the source's legacy days", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: { days: 3, network: "corp-apcn", sensors: ["XPress-SW-A"] } })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toMatch(/^Filled from this source's saved run defaults/));
    expect(screen.getByRole("button", { name: "Last 3 d of data" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("falls back to the built-ins for a source with neither", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days of data."));
    expect(select("Measurement interval").value).toBe("3600");
    expect(select("Burst resolution").selectedOptions[0]!.textContent).toBe("5 min");
  });

  it("a run that arrives after the operator chose keeps their choice, and fills the rest", async () => {
    let release = () => {};
    const { api, state } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })], runs: [pastRun()] });
    state.runsHold = new Promise<void>((r) => (release = r));
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days of data."));
    fireEvent.click(screen.getByRole("button", { name: "Last 24 h of data" }));
    release();
    await waitFor(async () => expect(await hint()).toMatch(/^Filled from run disc-8d560f14/));
    expect(screen.getByRole("button", { name: "Last 24 h of data" }).getAttribute("aria-pressed")).toBe("true");
    // The field the operator never touched does take the run's value.
    expect(select("Measurement interval").value).toBe("900");
  });

  it("only this source's runs count", async () => {
    const { api } = fakeApi({ plugins: [AXQUERY], sources: [tunnelSource({ defaults: {} })], runs: [pastRun({ source_id: "src-other" })] });
    renderAt(api, "/discovery/sources/src-1");
    await waitFor(async () => expect(await hint()).toBe("Built-in defaults: the last 10 days of data."));
  });
});

describe("the time range picker", () => {
  it("shows the span and zone, counts quick ranges from the end of the data, and estimates the cost", async () => {
    const { api } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    expect(screen.getByTestId("range-span").textContent).toMatch(/^Data held: 2026-09-06 19:30 UTC → 2026-10-06 19:30 UTC \(30\.0 days\)\. Times in .*UTC\+00:00\./);
    fireEvent.click(screen.getByRole("button", { name: "Last 24 h of data" }));
    expect(screen.getByTestId("range-selected").textContent).toMatch(/^Last 24 h of data: 2026-10-05 19:30 UTC → 2026-10-06 19:30 UTC/);
    // 1 of 30 days: a thirtieth of the census.
    expect(screen.getByTestId("range-estimate").textContent).toBe(
      "Estimate, scaled from the census assuming even spread: ≈ 1 chunks · 86.4 M packets · 86.4 GB",
    );
  });

  it("the search box narrows the quick ranges", async () => {
    const { api } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.change(screen.getByLabelText("Search ranges"), { target: { value: "span" } });
    const group = screen.getByRole("group", { name: "Quick ranges" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Entire span"]);
  });

  it("typed bounds are absolute, kept within the span, and sent as-is", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2027-01-01T00:00" } });
    expect(screen.getByTestId("range-clamped")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-10-06T13:30" } });
    expect(screen.getByTestId("range-selected").textContent).toMatch(/\(absolute, sent as-is\)$/);
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    expect(lastRunBody(state)).toMatchObject({ ts_from: Date.parse("2026-10-06T13:30:00Z") / 1000, ts_to: END });
    expect(lastRunBody(state)).not.toHaveProperty("days");
  });

  it("recently used offers this source's earlier ranges as absolute bounds", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })], runs: [pastRun()] });
    renderAt(api, "/discovery/sources/src-1");
    const recent = await screen.findByRole("group", { name: "Recently used" });
    fireEvent.click(within(recent).getByRole("button", { name: /disc-8d560f14/ }));
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    expect(lastRunBody(state)).toMatchObject({ ts_from: 1791311437, ts_to: 1791315037 });
  });

  it("refuses a range shorter than one measurement interval", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.change(select("Measurement interval"), { target: { value: "14400" } });
    fireEvent.click(screen.getByRole("button", { name: "Last 1 h of data" }));
    expect(screen.getByTestId("run-problem").textContent).toBe("The time range (1 h) is shorter than one measurement interval (4 h).");
    expect((screen.getByRole("button", { name: "Start discovery" }) as HTMLButtonElement).disabled).toBe(true);
    expect(state.writes.some((w) => w.op === "run")).toBe(false);
  });
});

describe("Advanced: add to an existing imprint", () => {
  const lineage = (over = {}) => ({
    version: 2, parent: "pit-a", root: "pit-a", network: "net-8d560f14", window_seconds: 900, subbuckets: 3,
    extendable: true, reason: null, next_name: "corp-apcn-v3", runs: [], ...over,
  });
  const imprints: Imprint[] = [
    { pit_id: "pit-b", name: "corp-apcn-v2", derived_from: null, has_assertions: false, twins: [], lineage: lineage() },
    { pit_id: "pit-m", name: "merged", derived_from: null, has_assertions: false, twins: [], lineage: lineage({ extendable: false, reason: "no belief store", version: 1 }) },
  ];

  it("fills interval and resolution from the base and locks them; off makes them editable again", async () => {
    const { api, state } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })], imprints });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.click(screen.getByLabelText("Add to an existing imprint"));
    const picker = (await screen.findByLabelText("Imprint to add to")) as HTMLSelectElement;
    await waitFor(() => expect(picker.options.length).toBe(3));
    const merged = Array.from(picker.options).find((o) => o.value === "pit-m")!;
    expect(merged.disabled).toBe(true);
    expect(merged.textContent).toContain("can't be extended: no belief store");
    fireEvent.change(picker, { target: { value: "pit-b" } });
    expect(screen.getByTestId("interval-locked").textContent).toBe("15 min · 5 min, from corp-apcn-v2 v2");
    expect(screen.queryByLabelText("Measurement interval")).toBeNull();
    expect((screen.getByLabelText("Imprint name") as HTMLInputElement).value).toBe("corp-apcn-v3");
    expect((screen.getByLabelText("Imprint name") as HTMLInputElement).readOnly).toBe(true);
    // Off: editable again, back at the defaults.
    fireEvent.click(screen.getByLabelText("Add to an existing imprint"));
    expect(screen.queryByTestId("interval-locked")).toBeNull();
    expect(select("Measurement interval").disabled).toBe(false);
    expect(select("Measurement interval").value).toBe("3600");
    // On again, and run.
    fireEvent.click(screen.getByLabelText("Add to an existing imprint"));
    fireEvent.change(screen.getByLabelText("Imprint to add to"), { target: { value: "pit-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Last 6 h of data" }));
    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));
    await waitFor(() => expect(state.writes.some((w) => w.op === "run")).toBe(true));
    const body = lastRunBody(state);
    expect(body).toMatchObject({ name: "corp-apcn-v3", base_imprint: "pit-b", days: 0.25 });
    expect(body).not.toHaveProperty("window");
    expect(body).not.toHaveProperty("subbuckets");
  });

  it("the interval check uses the base's interval", async () => {
    const { api } = fakeApi({ sources: [tunnelSource({ defaults: {}, last_probe: PROBE })], imprints: [{ ...imprints[0]!, lineage: lineage({ window_seconds: 14400, subbuckets: 12 }) }] });
    renderAt(api, "/discovery/sources/src-1");
    await screen.findByRole("button", { name: "Start discovery" });
    fireEvent.click(screen.getByLabelText("Add to an existing imprint"));
    const picker = (await screen.findByLabelText("Imprint to add to")) as HTMLSelectElement;
    await waitFor(() => expect(picker.options.length).toBe(2));
    fireEvent.change(picker, { target: { value: "pit-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Last 1 h of data" }));
    expect(screen.getByTestId("run-problem").textContent).toBe("The time range (1 h) is shorter than one measurement interval (4 h).");
  });
});
