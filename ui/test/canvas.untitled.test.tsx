/**
 * The document-style canvas (`ui/src/features/edit-canvas/SPEC.md`, "New, Save-As
 * on first save, Open"): New is an untitled canvas with no server read of a
 * stored twin; its Save waits for a segment, then asks for an id (checked
 * before any request) and writes through A3; Open lists the catalog and marks
 * a deployed twin; unsaved work asks before it is left.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readTe } from "../src/features/edit-canvas/edit/te";
import { UNTITLED_SCOPE } from "../src/features/edit-canvas/CanvasPage";
import { item, seeded, segments, settle } from "./helpers/canvasFixtures";
import { renderAt } from "./helpers/render";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

/** Every read keyed by a stored twin, recorded by name. */
function recordTwinReads(api: ReturnType<typeof seeded>["api"]) {
  const reads: string[] = [];
  for (const name of ["getTwin", "getTwinTree", "getTwinTopology", "getScenarioTopology", "getCast", "getDeployConfig", "getTwinConfig", "getLayout", "listDeploys"] as const) {
    const fn = (api[name] as (...a: unknown[]) => unknown).bind(api);
    (api as unknown as Record<string, unknown>)[name] = (...a: unknown[]) => {
      reads.push(`${name}(${String(a[0])})`);
      return fn(...a);
    };
  }
  return reads;
}

const saveButton = () => screen.getByTestId("save-untitled") as HTMLButtonElement;

async function addFirstSegment() {
  fireEvent.click(item("segment", "segment"));
  await waitFor(() => expect(segments()).toEqual(["segment-1"]));
}

describe("New: an untitled canvas", () => {
  it("opens editable and blank with no read of a stored twin", async () => {
    const { api, state } = seeded();
    const reads = recordTwinReads(api);
    renderAt(api, "/canvas/new");
    await screen.findByRole("heading", { name: "Untitled" });
    await screen.findByTestId("rail-add");
    expect(screen.getByTestId("mode").textContent).toContain("edit");
    expect(screen.getByTestId("mode").textContent).toContain("not saved yet");
    expect(screen.queryAllByTestId("canvas-segment")).toHaveLength(0);
    expect(screen.queryByTestId("deploy-open")).toBeNull();
    expect(screen.queryByLabelText("Save as id")).toBeNull();
    // The canvas is the page: no sub-bar, no form.
    expect(screen.queryByRole("navigation", { name: "Canvas" })).toBeNull();
    expect(document.querySelector("main.canvas-main")).not.toBeNull();
    await settle();
    expect(reads).toEqual([]);
    expect(state.writes.filter((w) => w.op !== "validate")).toEqual([]);
  });

  it("holds Save with the segment reason, then saves as the chosen id and becomes that design, clean", async () => {
    const { api, state } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    expect(saveButton().disabled).toBe(true);
    expect(saveButton().title).toBe("add at least one segment before saving");
    expect(screen.getByTestId("empty-reason").textContent).toBe("add at least one segment before saving");
    // No segment: nothing is sent to the validator, and no engine refusal is shown.
    await settle();
    expect(state.writes.filter((w) => w.op === "validate")).toEqual([]);

    await addFirstSegment();
    expect(screen.queryByTestId("empty-reason")).toBeNull();
    await waitFor(() => expect(saveButton().disabled).toBe(false));
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole("dialog", { name: "Save design" });
    fireEvent.change(within(dialog).getByLabelText("Id"), { target: { value: "my-lab" } });
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "a lab" } });
    const submit = within(dialog).getByRole("button", { name: "Save" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    await screen.findByRole("heading", { name: "my-lab" });
    const write = state.writes.find((w) => w.op === "create-twin-config")!;
    expect(write.id).toBe("my-lab");
    const files = (write.body as { files: Record<string, string> }).files;
    const te = readTe(files["te.yaml"]!);
    expect(te.id).toBe("my-lab");
    expect(te.description).toBe("a lab");
    expect(te.segments.map((s) => s.name)).toEqual(["segment-1"]);
    expect(files["twin.yaml"]).toContain("id: my-lab");
    expect(files["twin.yaml"]).toContain("description: a lab");
    expect(files["te.yaml"]).not.toContain("untitled");
    // The named canvas: clean, its Save a plain Save, the untitled browser copy gone.
    await waitFor(() => expect(screen.getByTestId("clean-marker").textContent).toBe("saved"));
    expect(screen.getByLabelText("Save as id")).toBeTruthy();
    expect(localStorage.getItem(`axr-canvas-undo:${UNTITLED_SCOPE}`)).toBeNull();
    // The undo stack moved with it: Undo takes the segment back off the named design.
    const undo = screen.getByRole("button", { name: "Undo" }) as HTMLButtonElement;
    await waitFor(() => expect(undo.disabled).toBe(false));
    fireEvent.click(undo);
    await waitFor(() => expect(screen.queryAllByTestId("canvas-segment")).toHaveLength(0));
    expect(screen.getByTestId("dirty-marker").textContent).toContain("unsaved changes");
  });

  it("refuses an invalid or taken id before any request", async () => {
    const { api, state } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    await addFirstSegment();
    await waitFor(() => expect(saveButton().disabled).toBe(false));
    fireEvent.click(saveButton());
    const dialog = await screen.findByRole("dialog", { name: "Save design" });
    const submit = within(dialog).getByRole("button", { name: "Save" }) as HTMLButtonElement;
    fireEvent.change(within(dialog).getByLabelText("Id"), { target: { value: "bad id" } });
    await waitFor(() => expect(within(dialog).getByTestId("saveas-problem").textContent).toMatch(/the id must start/));
    expect(submit.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Id"), { target: { value: "corp-auth" } });
    await waitFor(() => expect(within(dialog).getByTestId("saveas-problem").textContent).toBe("'corp-auth' is already in the catalog; pick another id"));
    expect(submit.disabled).toBe(true);
    // A cast twin's id is taken too, though it is not in the twin-config listing.
    fireEvent.change(within(dialog).getByLabelText("Id"), { target: { value: "corp-cast" } });
    await waitFor(() => expect(within(dialog).getByTestId("saveas-problem").textContent).toMatch(/already in the catalog/));
    fireEvent.submit(dialog);
    expect(state.writes.some((w) => w.op === "create-twin-config")).toBe(false);
    expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
  });

  it("keeps the untitled design across a reload of /canvas/new", async () => {
    const { api } = seeded();
    const first = renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    await addFirstSegment();
    first.unmount();
    renderAt(api, "/canvas/new");
    await waitFor(() => expect(segments()).toEqual(["segment-1"]));
    expect(screen.getByTestId("dirty-marker").textContent).toContain("unsaved changes");
    expect((screen.getByRole("button", { name: "Undo" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("the unsaved-work guard", () => {
  it("asks before a dirty untitled design is left by a link, and stays when declined", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    await addFirstSegment();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("link", { name: "Twins" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0]![0]).toMatch(/untitled design/);
    expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
    expect(segments()).toEqual(["segment-1"]);
    // Agreed: the page goes, and the untitled copy with it -- the next New is blank.
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("link", { name: "Twins" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Untitled" })).toBeNull());
    expect(localStorage.getItem(`axr-canvas-undo:${UNTITLED_SCOPE}`)).toBeNull();
  });

  it("does not ask while the untitled design is still blank", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("link", { name: "Twins" }));
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Untitled" })).toBeNull());
  });

  it("starts a fresh untitled design from New design once the old one is let go", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    await addFirstSegment();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Open a design" }));
    const menu = await screen.findByRole("menu", { name: "Designs" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: "New design" }));
    await waitFor(() => expect(screen.queryAllByTestId("canvas-segment")).toHaveLength(0));
    expect(screen.getByRole("heading", { name: "Untitled" })).toBeTruthy();
    expect(screen.getByTestId("clean-marker").textContent).toBe("not saved yet");
  });
});

describe("Open", () => {
  it("lists the twins with their origin, marks the deployed one, and opens the pick", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/new");
    await screen.findByTestId("rail-add");
    fireEvent.keyDown(window, { key: "o", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: "Open a design" });
    const rows = await within(dialog).findAllByTestId("open-row");
    expect(rows.map((r) => within(r).getByRole("link").textContent)).toEqual(["corp-auth", "corp-live", "corp-cast"]);
    expect(rows.map((r) => r.dataset["deployed"] ?? "")).toEqual(["", "true", ""]);
    expect(within(rows[1]!).getByTestId("open-deployed").textContent).toBe("deployed · dev45");
    expect(rows[0]!.textContent).toContain("authored");
    expect(rows[2]!.textContent).toContain("cast from corp capture");
    fireEvent.change(within(dialog).getByLabelText("Search designs"), { target: { value: "live" } });
    await waitFor(() => expect(within(dialog).getAllByTestId("open-row")).toHaveLength(1));
    fireEvent.click(within(dialog).getByRole("link", { name: "corp-live" }));
    await screen.findByRole("heading", { name: "corp-live" });
    expect(screen.queryByRole("dialog", { name: "Open a design" })).toBeNull();
  });

  it("is in the bar of a named design, and in the Canvas menu", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/corp-auth");
    await screen.findByRole("heading", { name: "corp-auth" });
    fireEvent.click(screen.getByRole("button", { name: "Open…" }));
    await screen.findByRole("dialog", { name: "Open a design" });
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Open a design" }));
    const menu = await screen.findByRole("menu", { name: "Designs" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Open…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open a design" });
    await within(dialog).findAllByTestId("open-row");
  });

  it("goes through the guard from a dirty undeployed design", async () => {
    const { api } = seeded();
    renderAt(api, "/canvas/corp-auth");
    await screen.findAllByTestId("canvas-host");
    fireEvent.click(item("segment", "segment"));
    await waitFor(() => expect(segments().length).toBe(3));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Open…" }));
    const dialog = await screen.findByRole("dialog", { name: "Open a design" });
    fireEvent.click(await within(dialog).findByRole("link", { name: "corp-cast" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0]![0]).toMatch(/unsaved changes/);
    expect(screen.getByRole("heading", { name: "corp-auth" })).toBeTruthy();
  });
});
