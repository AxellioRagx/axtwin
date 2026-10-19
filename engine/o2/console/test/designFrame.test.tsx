/**
 * The Design area's consolidation fixes (ruling §2, brief item 9): Apply is
 * disabled WITH the reason when the server would refuse it, Save says what it
 * is missing instead of being silently disabled, and the "live" label that
 * meant "would deploy" is gone.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { applyBlockedReason } from "../src/App";
import { initialEditState } from "../src/edit/store";
import { EditPanel, saveNeeds } from "../src/views/EditPanel";
import { apiError } from "./helpers/runtime";

// Resolved from this file, not the process cwd (LES-208); `__dirname` rather
// than `import.meta.url`, which is not a file URL under jsdom (LES-260).
const src = (file: string): string => readFileSync(join(__dirname, "..", file), "utf8");

describe("Apply is refused BEFORE the click, with the SERVER's reason", () => {
  it("can_apply false disables Apply and says the server's reason", () => {
    expect(applyBlockedReason({ can_apply: false, apply_blocked_reason: "a twins/ folder cannot be recompiled" }, null)).toBe(
      "Apply is not available: a twins/ folder cannot be recompiled",
    );
    expect(applyBlockedReason({ can_apply: false, apply_blocked_reason: null }, null)).toMatch(/gives no reason/);
  });

  it("can_apply true, or an older server that says nothing, is not guessed at", () => {
    expect(applyBlockedReason({ can_apply: true, apply_blocked_reason: null }, null)).toBeNull();
    expect(applyBlockedReason({}, null)).toBeNull();
    expect(applyBlockedReason(null, null)).toBeNull();
  });

  it("the /generated/ path heuristic is gone", () => {
    expect(src("src/App.tsx")).not.toMatch(/generated\[/);
  });

  it("an aximprint that is not configured is remembered from the first 503", () => {
    expect(applyBlockedReason(null, apiError("unavailable", 503, "AXRANGE_AXIMPRINT_BIN is not set"))).toMatch(
      /not available on this server: AXRANGE_AXIMPRINT_BIN is not set/,
    );
    expect(applyBlockedReason(null, apiError("upstream-failed", 502, "compiler failed"))).toBeNull();
  });

  it("the Apply button is disabled and the reason is on screen", () => {
    render(
      <EditPanel
        edits={initialEditState("x")}
        assertions={null}
        saving={false}
        onRemove={() => undefined}
        onSave={() => undefined}
        onReload={() => undefined}
        onApply={() => undefined}
        applyBlockedReason="Apply is not available for this twin: it lives in a twins/ folder."
        applying={false}
        applyResult={null}
        applyError={null}
        changedCount={0}
        diffAvailable={false}
        source="operator: console"
        onSourceChange={() => undefined}
        asOf="2026-09-29T00:00:00Z"
        onAsOfChange={() => undefined}
      />,
    );
    expect((screen.getByTestId("apply-button") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("save-button") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("apply-blocked-reason").textContent).toMatch(/twins\/ folder/);
    expect(screen.queryByTestId("save-blocked-reason")).toBeNull();
  });
});

describe("Save says what it needs", () => {
  it("names each missing piece", () => {
    const edits = initialEditState("x");
    expect(saveNeeds(edits, "operator: ", "")).toEqual(["a source of the form operator: <your name>"]);
    expect(saveNeeds(edits, "operator: me", "")).toEqual(["an as_of timestamp"]);
    expect(saveNeeds({ ...edits, mustReload: true }, "operator: me", "t")[0]).toMatch(/reload the file first/);
  });

  it("a new file is seeded with a VALID source, so the first edit can be saved", () => {
    const app = src("src/App.tsx");
    expect(app).toMatch(/const DEFAULT_SOURCE = "operator: console";/);
    expect(app).toMatch(/setSource\(assertions\.data\.source \?\? DEFAULT_SOURCE\)/);
  });
});

describe("design-time counts are not called live", () => {
  it("no Design view labels the would-deploy count 'live'", () => {
    for (const file of ["src/views/Level0.tsx", "src/views/ElementActions.tsx"]) {
      const text = src(file);
      expect(text, file).not.toMatch(/deployedCount\}? live/);
      expect(text, file).toMatch(/would deploy/);
    }
    expect(src("src/views/Level2.tsx")).not.toMatch(/S5 authors these/);
  });
});
