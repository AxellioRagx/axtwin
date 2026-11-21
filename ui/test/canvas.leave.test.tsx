/**
 * Round 3 item 4 (`SPEC.md` "Leaving unsaved work"): an unsaved Untitled design was replaced with no
 * prompt when Open's list loaded under the pointer and the click picked a twin. Now switching designs
 * with unsaved work asks Save as draft / Discard / Cancel (Open, the logo, Back), and Open's rows take
 * a click only once they have been where they are for ROW_SETTLE_MS.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ROW_SETTLE_MS } from "../src/areas/twins/TwinList";
import { UNTITLED_SCOPE } from "../src/features/edit-canvas/CanvasPage";
import { addSegment, seeded, segments } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => localStorage.clear());

const KEY = `axr-canvas-undo:${UNTITLED_SCOPE}`;
const rowsSettle = () => new Promise((r) => setTimeout(r, ROW_SETTLE_MS + 30));
const openDialog = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Open…" }));
  return screen.findByRole("dialog", { name: "Open a twin" });
};

/** An untitled design with one segment, restored from this browser as a page open finds it. */
async function restoredUntitled() {
  const { api, state } = seeded();
  const first = renderAt(api, "/canvas/new");
  await screen.findByTestId("rail-add");
  addSegment();
  await waitFor(() => expect(segments()).toEqual(["segment-1"]));
  expect(localStorage.getItem(KEY)).not.toBeNull();
  first.unmount();
  renderAt(api, "/canvas/new");
  await waitFor(() => expect(segments()).toEqual(["segment-1"]));
  return { api, state };
}

describe("leaving unsaved work", () => {
  it("an untitled design with work keeps an undo entry in this browser", async () => {
    await restoredUntitled();
    const kept = JSON.parse(localStorage.getItem(KEY)!) as { stack: { done: unknown[] } };
    expect(kept.stack.done.length).toBeGreaterThan(0);
  });

  it("the round-2 misclick: a click on a row that just loaded is ignored; a settled one asks; Cancel keeps the design", async () => {
    await restoredUntitled();
    const dialog = await openDialog();
    const rows = await within(dialog).findAllByTestId("open-row");
    fireEvent.click(within(rows[2]!).getAllByRole("link")[0]!);
    fireEvent.click(rows[2]!.querySelectorAll("td")[3]!);
    expect(screen.queryByTestId("leave-dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
    await rowsSettle();
    fireEvent.click(within(rows[2]!).getAllByRole("link")[0]!);
    const leave = await screen.findByTestId("leave-dialog");
    expect(within(leave).getAllByRole("button").map((b) => b.textContent)).toEqual(["Save as draft…", "Discard", "Cancel"]);
    fireEvent.click(within(leave).getByTestId("leave-cancel"));
    expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
    expect(segments()).toEqual(["segment-1"]);
    expect(localStorage.getItem(KEY)).not.toBeNull();
  });

  it("Save as draft from the question writes the draft, then opens the twin that was picked", async () => {
    const { state } = await restoredUntitled();
    const dialog = await openDialog();
    const link = (await within(dialog).findByRole("link", { name: "corp-cast" })) as HTMLAnchorElement;
    await rowsSettle();
    fireEvent.click(link);
    fireEvent.click(within(await screen.findByTestId("leave-dialog")).getByTestId("leave-draft"));
    const draft = await screen.findByTestId("draft-dialog");
    fireEvent.change(within(draft).getByLabelText("Id"), { target: { value: "kept-untitled" } });
    fireEvent.submit(draft);
    await screen.findByRole("heading", { name: "corp-cast" });
    expect(state.writes.some((w) => w.op === "create-draft" && w.id === "kept-untitled")).toBe(true);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("Discard leaves and drops the untitled copy", async () => {
    await restoredUntitled();
    const dialog = await openDialog();
    const link = await within(dialog).findByRole("link", { name: "corp-cast" });
    await rowsSettle();
    fireEvent.click(link);
    fireEvent.click(within(await screen.findByTestId("leave-dialog")).getByTestId("leave-discard"));
    await screen.findByRole("heading", { name: "corp-cast" });
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("the browser's Back asks too, over the page that is still there", async () => {
    await restoredUntitled();
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
    const leave = await screen.findByTestId("leave-dialog");
    expect(leave.textContent).toMatch(/Going back leaves it/);
    fireEvent.click(within(leave).getByTestId("leave-cancel"));
    expect(segments()).toEqual(["segment-1"]);
  });

  it("the logo (a new design) asks from a dirty named design", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/corp-auth");
    await screen.findAllByTestId("canvas-host");
    addSegment();
    await waitFor(() => expect(segments().length).toBe(3));
    fireEvent.click(screen.getByRole("link", { name: "XTwin: a new design on the canvas" }));
    const leave = await screen.findByTestId("leave-dialog");
    expect(leave.textContent).toMatch(/unsaved changes/);
    fireEvent.click(within(leave).getByTestId("leave-cancel"));
    expect(screen.getByRole("heading", { name: "corp-auth" })).toBeTruthy();
  });
});
