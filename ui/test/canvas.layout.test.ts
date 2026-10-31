/**
 * The layout side-car as a draft (`edit/useLayoutDraft.ts`): moves and
 * annotation changes stay local and count as unsaved; `save` is the one
 * write, carrying everything as it stands with the etag as the precondition;
 * `discard` returns to the last loaded or saved layout; a 412 takes the fresh
 * etag so the next save replaces the other console's layout, said so.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LayoutDoc } from "../src/api/types";
import { CANVAS_PLANE } from "../src/features/edit-canvas/canvas/plane";
import { layoutWrite, pinsOf, useLayoutDraft } from "../src/features/edit-canvas/edit/useLayoutDraft";
import { fakeApi } from "./helpers/fakeApi";
import { twin } from "./imprints.page.test";

function doc(over: Partial<LayoutDoc> = {}): LayoutDoc {
  return { imprint_id: "corp-auth", path: "/store/twins/corp-auth/generated/layout.json", exists: true, etag: "layout-etag-1", schema: "axr-layout/1", imprint: "corp-auth", pins: { "host:ws01": { x: 10, y: 20 } }, plane: CANVAS_PLANE, annotations: [], ...over };
}

function seeded(loaded: LayoutDoc | null) {
  const { api, state } = fakeApi({ twins: { "corp-auth": twin("corp-auth") }, layouts: loaded ? { "corp-auth": loaded } : {} });
  const hook = renderHook(({ l }: { l: LayoutDoc | null }) => useLayoutDraft(api, "corp-auth", l), { initialProps: { l: loaded } });
  const writes = () => state.writes.filter((w) => w.op === "put-layout");
  return { api, state, hook, writes };
}

describe("the layout draft", () => {
  it("moves count as unsaved and write nothing; Discard returns to the loaded layout", () => {
    const { hook, writes } = seeded(doc());
    expect(hook.result.current.pins).toEqual({ "host:ws01": { x: 10, y: 20 } });
    expect(hook.result.current.changes).toBe(0);
    act(() => hook.result.current.onPin("host:ws01", { x: 1, y: 2 }));
    act(() => hook.result.current.onPin("segment:10.1.0.0/24", { x: 3, y: 4 }));
    expect(hook.result.current.changes).toBe(2);
    expect(hook.result.current.pins).toEqual({ "host:ws01": { x: 1, y: 2 }, "segment:10.1.0.0/24": { x: 3, y: 4 } });
    expect(writes()).toEqual([]);
    act(() => hook.result.current.discard());
    expect(hook.result.current.changes).toBe(0);
    expect(hook.result.current.pins).toEqual({ "host:ws01": { x: 10, y: 20 } });
    expect(writes()).toEqual([]);
  });

  it("Save is one PUT of every pin and annotation as they stand, the loaded etag as the precondition", async () => {
    const { hook, writes, state } = seeded(doc());
    act(() => hook.result.current.onPin("host:ws01", { x: 1, y: 2 }));
    act(() => hook.result.current.onPin("host:web01", { x: 5, y: 6 }));
    act(() => hook.result.current.setAnnotations([{ id: "a1", kind: "rect", x: 0, y: 0, w: 10, h: 10, text: "", locked: false, hidden: false }]));
    expect(hook.result.current.changes).toBe(3);
    let ok = false;
    await act(async () => {
      ok = await hook.result.current.save();
    });
    expect(ok).toBe(true);
    expect(writes().length).toBe(1);
    expect(writes()[0]!.body).toMatchObject({ schema: "axr-layout/1", imprint: "corp-auth", plane: CANVAS_PLANE, etag: "layout-etag-1", pins: { "host:ws01": { x: 1, y: 2 }, "host:web01": { x: 5, y: 6 } } });
    expect((writes()[0]!.body as { annotations: Array<{ id: string; svg: string }> }).annotations.map((a) => a.id)).toEqual(["a1"]);
    expect(hook.result.current.changes).toBe(0);
    expect(hook.result.current.savedAt).not.toBeNull();
    expect(hook.result.current.doc?.etag).toBe(state.layouts["corp-auth"]!.etag);
    // The next save uses the etag the first returned; a document with no annotations carries none.
    const etagAfterFirst = hook.result.current.doc!.etag;
    act(() => hook.result.current.setAnnotations([]));
    await act(async () => {
      await hook.result.current.save();
    });
    expect((writes()[1]!.body as { etag: string | null }).etag).toBe(etagAfterFirst);
    expect((writes()[1]!.body as { annotations?: unknown }).annotations).toBeUndefined();
  });

  it("ignores pins authored on another plane: a first save carries only this plane's moves", async () => {
    const { hook, writes } = seeded(doc({ plane: "l0-3-deadbeef", pins: { "site:misc": { x: 1200, y: 300 } } }));
    expect(hook.result.current.pins).toEqual({});
    act(() => hook.result.current.onPin("segment:10.1.0.0/24", { x: 5, y: 6 }));
    await act(async () => {
      await hook.result.current.save();
    });
    expect((writes()[0]!.body as { pins: unknown }).pins).toEqual({ "segment:10.1.0.0/24": { x: 5, y: 6 } });
    expect(pinsOf(null)).toEqual({});
    expect(layoutWrite(null, "x", {}, []).etag).toBeNull();
  });

  it("a 412 keeps the draft, takes the fresh etag and says so; the next save goes through", async () => {
    const { hook, writes, state } = seeded(doc());
    // Another console saved meanwhile: the store's etag moved on.
    state.layouts["corp-auth"] = doc({ etag: "layout-etag-9", pins: { "host:web01": { x: 7, y: 7 } } });
    act(() => hook.result.current.onPin("host:ws01", { x: 1, y: 2 }));
    let ok = true;
    await act(async () => {
      ok = await hook.result.current.save();
    });
    expect(ok).toBe(false);
    expect(hook.result.current.changes).toBe(1);
    expect(hook.result.current.pins["host:ws01"]).toEqual({ x: 1, y: 2 });
    await waitFor(() => expect(hook.result.current.note).toMatch(/another console saved this layout meanwhile/));
    expect((hook.result.current.error as { status: number }).status).toBe(412);
    expect(hook.result.current.doc?.etag).toBe("layout-etag-9");
    await act(async () => {
      ok = await hook.result.current.save();
    });
    expect(ok).toBe(true);
    expect(writes().map((w) => (w.body as { etag: string | null }).etag)).toEqual(["layout-etag-1", "layout-etag-9"]);
    expect(hook.result.current.note).toBeNull();
    expect(hook.result.current.error).toBeNull();
    expect(state.layouts["corp-auth"]!.pins).toEqual({ "host:ws01": { x: 1, y: 2 } });
  });

  it("a reload replaces the draft", () => {
    const { hook } = seeded(doc());
    act(() => hook.result.current.onPin("host:ws01", { x: 1, y: 2 }));
    expect(hook.result.current.changes).toBe(1);
    hook.rerender({ l: doc({ etag: "layout-etag-2", pins: { "host:ws01": { x: 99, y: 99 } } }) });
    expect(hook.result.current.changes).toBe(0);
    expect(hook.result.current.pins).toEqual({ "host:ws01": { x: 99, y: 99 } });
    expect(hook.result.current.doc?.etag).toBe("layout-etag-2");
  });
});
