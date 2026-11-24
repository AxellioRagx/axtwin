/**
 * The deploy view's traffic (canvas task item 6): on a twin with a standing
 * tfc deploy, the twin page's deploy panel and the canvas's Traffic button
 * show the planned generators with their ops and each line's live counters
 * (`GET /api/deploys/{tfc}` and `GET /api/tfc?deploy_id=`).
 */
import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DeployDetail, DeploySummary, TrafficStatus } from "../src/api/types";
import { DEPLOY, seeded } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

const TFC: DeploySummary = { deploy_id: "tfc-dep-1-0001", scenario_id: "tfc-dep-1", status: "completed", started_at: 1700000200, updated_at: 1700000300, tier: "tfc", target: "dev45" };
const op = (op_name: string, scope: Record<string, string>, status = "completed") => ({ op_id: `${op_name}-${Object.values(scope).join("-")}`, op_name, op_catalog_id: "x", kind: "infra", status, scope });
const DETAIL: DeployDetail = {
  deploy: TFC, plan_id: "tfc-dep-1", placement: {}, counts: { completed: 7 },
  ops: [
    op("drive-persona", { instance: "rack2-web01-web-tier" }),
    op("clone-vm", { host: "gen-internet--rack1-lan" }),
    op("attach-vm-to-vnet", { host: "gen-internet--rack1-lan", net: "internet" }),
    op("attach-vm-to-switch-port", { host: "gen-internet--rack1-lan", net: "rack1-lan" }),
    op("provision-generator", { generator: "gen-internet--rack1-lan" }),
    op("load-traffic-profile", { generator: "gen-internet--rack1-lan" }),
    op("start-traffic", { generator: "gen-internet--rack1-lan" }, "running"),
  ],
};
const STATUS: TrafficStatus = {
  deploy_id: TFC.deploy_id, twin: "tfc-dep-1", status: "completed", assignments: [], warnings: [],
  lines: { "internet-users--rack1-web--web-browse": { generator: "gen-internet--rack1-lan", from: "internet-users", to: "rack1-web", mix: "web-browse", rate_cps: 30, state: "running", established: 1821, errors: 0, open: 9, bytes: 4200000, cps_now: 30.1 } },
};

function withTfc() {
  const s = seeded({ deploys: [{ ...DEPLOY, follow_on: { tfc: { status: "completed", deploy_id: TFC.deploy_id } } }, TFC], deployDetails: { [TFC.deploy_id]: DETAIL }, tfcStatus: { [TFC.deploy_id]: STATUS } });
  return s;
}

describe("the deploy view shows the generators and the lines' counters", () => {
  it("on the twin page's deploy panel", async () => {
    const { api } = withTfc();
    renderAt(api, "/twins/corp-live");
    const live = within(await screen.findByTestId("traffic-live"));
    const gen = await live.findByTestId("generator");
    expect(gen.dataset["generator"]).toBe("gen-internet--rack1-lan");
    expect(gen.textContent).toContain("5/6 ops completed");
    expect(live.getAllByTestId("generator-op").map((o) => `${o.dataset["op"]}:${o.dataset["status"]}`)).toContain("start-traffic:running");
    expect(live.getByTestId("traffic-live-summary").textContent).toBe("1 generator · 1/1 real persona ops completed");
    const row = await live.findByTestId("line-counter");
    expect(row.dataset["line"]).toBe("internet-users--rack1-web--web-browse");
    expect(within(row).getByTestId("line-established").textContent).toBe("1,821");
  });

  it("behind the canvas's Traffic button on a deployed twin", async () => {
    const { api } = withTfc();
    renderAt(api, "/twins/corp-live/canvas");
    fireEvent.click(await screen.findByTestId("traffic-open"));
    const pop = within(await screen.findByTestId("traffic-pop"));
    expect((await pop.findByTestId("line-counter")).dataset["line"]).toBe("internet-users--rack1-web--web-browse");
  });
});
