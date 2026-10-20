/**
 * Destroy from the Twin page (rule 6): the cascade is stated, the button arms
 * only on the deploy id typed back exactly, and a double submit sends ONE
 * DELETE. The result -- including sibling cascade errors -- is shown, not
 * assumed.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RuntimeApi } from "../src/api/runtime";
import type { DestroyResponse } from "../src/api/runtimeTypes";
import { cascadeLines, destroyArmed } from "../src/areas/twin/DestroyDialog";
import { TestApp } from "../src/routes";
import { apiError, corpTwinHosts, fakeRuntime, unusedDesignApi } from "./helpers/runtime";

const ID = "corp-twin-07cfcdb2";

function open(overrides: Partial<RuntimeApi>, calls: string[] = []): void {
  const runtime = fakeRuntime(
    {
      getHosts: () => Promise.resolve(corpTwinHosts()),
      getTargets: () => Promise.resolve({ targets: [] }),
      ...overrides,
    },
    calls,
  );
  render(<TestApp client={unusedDesignApi()} runtime={runtime} url={`/twin/${ID}`} />);
}

describe("the typed confirmation", () => {
  it("arms only on the exact deploy id (whitespace forgiven)", () => {
    expect(destroyArmed("", ID)).toBe(false);
    expect(destroyArmed("corp-twin", ID)).toBe(false);
    expect(destroyArmed(`${ID}x`, ID)).toBe(false);
    expect(destroyArmed(ID.toUpperCase(), ID)).toBe(false);
    expect(destroyArmed(`  ${ID} `, ID)).toBe(true);
  });

  it("states the cascade in teardown order, naming what is recorded and what is not", () => {
    expect(cascadeLines({ tfc: { deploy_id: "tfc-1", status: "completed" }, tap: null }, 3)).toEqual([
      "any exercise run against this twin",
      "the traffic personas (traffic deploy tfc-1)",
      "taps: none recorded",
      "the twin itself: 3 VMs and its networks",
    ]);
  });
});

describe("destroying from the Twin page", () => {
  it("shows the cascade, arms on the typed id, and sends one DELETE", async () => {
    const calls: string[] = [];
    let resolve!: (r: DestroyResponse) => void;
    open({ destroyDeploy: () => new Promise<DestroyResponse>((r) => (resolve = r)) }, calls);
    fireEvent.click(await screen.findByTestId("destroy-open"));
    const dialog = screen.getByTestId("destroy-dialog");
    const cascade = within(dialog).getByTestId("destroy-cascade").textContent ?? "";
    expect(cascade).toMatch(/exercise/);
    expect(cascade).toMatch(/traffic personas \(traffic deploy tfc-corp-twin-07cfcdb2-785b1a06\)/);
    expect(cascade).toMatch(/taps mirroring it to sensors \(tap deploy tap-corp-twin-07cfcdb2-8176c5ad\)/);
    expect(cascade).toMatch(/106 VMs/);
    expect(dialog.textContent).toMatch(/on dev45/);

    const confirm = screen.getByTestId("destroy-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("destroy-typed"), { target: { value: "corp-twin" } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("destroy-typed"), { target: { value: ID } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(await screen.findByTestId("destroy-progress")).toBeTruthy();
    expect(calls.filter((c) => c === "destroyDeploy")).toHaveLength(1);

    resolve({ deploy_id: ID, destroyed: true, removed: 212, errors: 0, actions: [], cascade_errors: ["tap: sensor unreachable"] });
    const result = await screen.findByTestId("destroy-result");
    expect(result.textContent).toMatch(/Destroyed\. 212 resources removed, 0 errors/);
    expect(result.textContent).toMatch(/Still standing and needs a follow-up destroy: tap: sensor unreachable/);
  });

  it("Escape or Cancel closes it without a request", async () => {
    const calls: string[] = [];
    open({}, calls);
    fireEvent.click(await screen.findByTestId("destroy-open"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("destroy-dialog")).toBeNull();
    expect(calls).not.toContain("destroyDeploy");
  });
});

describe("destroying a PROTECTED twin", () => {
  it("needs the override step as well as the typed id, and sends the override", async () => {
    const doc = corpTwinHosts();
    doc.protected = true;
    const sent: Array<string | null | undefined> = [];
    open({
      getHosts: () => Promise.resolve(doc),
      destroyDeploy: (_id, _t, override) => {
        sent.push(override);
        return new Promise<DestroyResponse>(() => undefined);
      },
    });
    // The header's Destroy is LOCKED: muted, padlocked, the reason beside it,
    // and activating it opens the override step -- not the dialog.
    const locked = await screen.findByTestId("destroy-open");
    expect(locked.getAttribute("data-locked")).toBe("true");
    expect(locked.className).not.toMatch(/danger|primary/);
    expect(screen.getByTestId("destroy-open-reason").textContent).toBe("Protected — writes are refused unless you override by name");
    fireEvent.click(locked);
    expect(screen.queryByTestId("destroy-dialog")).toBeNull();
    fireEvent.change(screen.getByTestId("protection-typed"), { target: { value: ID } });
    fireEvent.click(screen.getByTestId("protection-confirm"));
    // Overridden for this page: Destroy is live again, and the dialog still
    // needs the deploy id typed back.
    fireEvent.click(screen.getByTestId("destroy-open"));
    const confirm = screen.getByTestId("destroy-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("destroy-typed"), { target: { value: ID } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await waitFor(() => expect(sent).toEqual([ID]));
  });

  it("a 423 from a twin the page did not know was protected returns to the confirm step with the override offered", async () => {
    open({ destroyDeploy: () => Promise.reject(apiError("locked", 423, "deploy is protected", { code: "protected" })) });
    fireEvent.click(await screen.findByTestId("destroy-open"));
    fireEvent.change(screen.getByTestId("destroy-typed"), { target: { value: ID } });
    fireEvent.click(screen.getByTestId("destroy-confirm"));
    expect((await screen.findByTestId("destroy-locked")).textContent).toMatch(/protected/);
    expect(screen.getByTestId("protection-gate")).toBeTruthy();
    expect((screen.getByTestId("destroy-confirm") as HTMLButtonElement).disabled).toBe(true);
  });
});

