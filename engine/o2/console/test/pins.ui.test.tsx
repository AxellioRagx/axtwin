/**
 * S4 pins -- the jsdom half of the acceptance (`docs/WORK_ORDER_console_canvas.md`
 * §3 S4): the drag GESTURE (`usePinDrag`) and the UI AFFORDANCE on Level0.
 * The pure layout half (locality / reset / placement / host-guard) is
 * `pins.test.ts`.
 */
import { fireEvent, render, renderHook, act, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PointerEvent as ReactPointerEvent } from "react";

import { Level0 } from "../src/views/Level0";
import { usePinDrag } from "../src/views/usePinDrag";
import { layout0 } from "../src/layout/level0";
import { buildGraph } from "../src/model/graph";
import { NO_FILTERS } from "../src/model/zoom";
import { trimmedCast, trimmedExport } from "./fixtures/trimmed";

const graph = buildGraph(trimmedExport, trimmedCast);

/** A stub `<svg>` whose viewBox matches its rect 1:1, so a client delta maps
 *  to an equal layout delta and the drag math is deterministic without a real
 *  browser (jsdom has no SVG layout or CTM). */
function fakeSvg(): SVGSVGElement {
  return {
    getScreenCTM: () => null,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    viewBox: { baseVal: { x: 0, y: 0, width: 100, height: 100 } },
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
  } as unknown as SVGSVGElement;
}

function ptr(over: Partial<{ button: number; pointerId: number; clientX: number; clientY: number }>): ReactPointerEvent {
  return { button: 0, pointerId: 1, clientX: 0, clientY: 0, ...over } as unknown as ReactPointerEvent;
}

describe("usePinDrag -- the drag gesture", () => {
  it("a drag past the threshold commits an absolute pin and swallows the trailing click", () => {
    const onCommit = vi.fn();
    const svgRef = { current: fakeSvg() };
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));

    // Grab a tile displayed at (100, 200); the drag starts AT the origin.
    act(() => result.current.begin("segment:10.80.20.0/24", { x: 100, y: 200 }, ptr({ clientX: 10, clientY: 10 })));
    expect(result.current.draggedKey).toBe("segment:10.80.20.0/24");
    expect(result.current.effectivePins["segment:10.80.20.0/24"]).toEqual({ x: 100, y: 200 });

    // Move +50/+30 client px -> +50/+30 layout units (1:1 stub) -> live pin.
    act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 60, clientY: 40 })));
    expect(result.current.effectivePins["segment:10.80.20.0/24"]).toEqual({ x: 150, y: 230 });

    act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 60, clientY: 40 })));
    expect(onCommit).toHaveBeenCalledTimes(1);
    // R2/M1 widened the commit to `(pins, authored)`: the second argument is
    // the one pin THIS gesture wrote, by key AND value, so a pin the operator
    // never touched can never be stamped as authored against the plane now on
    // screen (LES-289). The value is what survives another writer replacing
    // the document underneath us.
    expect(onCommit).toHaveBeenCalledWith(
      { "segment:10.80.20.0/24": { x: 150, y: 230 } },
      { key: "segment:10.80.20.0/24", at: { x: 150, y: 230 } },
    );
    expect(result.current.draggedKey).toBeNull();
    // The click that follows a real drag is swallowed exactly once.
    expect(result.current.consumeDragClick("segment:10.80.20.0/24")).toBe(true);
    expect(result.current.consumeDragClick("segment:10.80.20.0/24")).toBe(false);
  });

  it("a pointerdown+up with no real move is a click (drill), not a pin", () => {
    const onCommit = vi.fn();
    const svgRef = { current: fakeSvg() };
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));

    act(() => result.current.begin("segment:x", { x: 0, y: 0 }, ptr({ clientX: 10, clientY: 10 })));
    act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 11, clientY: 11 }))); // < 4px
    expect(onCommit).not.toHaveBeenCalled();
    expect(result.current.consumeDragClick("segment:x")).toBe(false);
  });

  it("merges the drag onto EXISTING saved pins (nudging one, keeping the rest)", () => {
    const onCommit = vi.fn();
    const svgRef = { current: fakeSvg() };
    const saved = { "site:10.20": { x: 1, y: 2 }, "segment:10.80.20.0/24": { x: 100, y: 200 } };
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: saved, onCommit }));

    act(() => result.current.begin("segment:10.80.20.0/24", { x: 100, y: 200 }, ptr({ clientX: 0, clientY: 0 })));
    act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 5, clientY: 7 })));
    act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 5, clientY: 7 })));
    // The commit carries BOTH pins -- and reports that only the dragged one
    // was written (R2/M1), which is exactly what stops the untouched
    // `site:10.20` being re-stamped as current off this single gesture.
    expect(onCommit).toHaveBeenCalledWith(
      {
        "site:10.20": { x: 1, y: 2 },
        "segment:10.80.20.0/24": { x: 105, y: 207 },
      },
      { key: "segment:10.80.20.0/24", at: { x: 105, y: 207 } },
    );
  });

  it("is inert on a read-only render (no writer) and on a non-primary button", () => {
    const svgRef = { current: fakeSvg() };
    const readOnly = renderHook(() => usePinDrag({ svgRef, savedPins: {} }));
    act(() => readOnly.result.current.begin("segment:x", { x: 0, y: 0 }, ptr({ clientX: 0, clientY: 0 })));
    expect(readOnly.result.current.draggedKey).toBeNull();
    expect(readOnly.result.current.effectivePins).toEqual({});

    const onCommit = vi.fn();
    const withWriter = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));
    act(() => withWriter.result.current.begin("segment:x", { x: 0, y: 0 }, ptr({ button: 2, clientX: 0, clientY: 0 })));
    expect(withWriter.result.current.draggedKey).toBeNull(); // right-click ignored
  });

  // --- S1: the trailing-click suppressor must not eat a LATER genuine drill ---
  it("does NOT eat a later drill when the drag's trailing click routed to the <svg> (S1)", () => {
    // With pointer capture, several browsers dispatch the compat click to the
    // capturing <svg>, so `consumeDragClick` is NEVER called for the drag's own
    // click and the flag stays armed. A time bound (not "the click arrived")
    // lets it expire so the operator's next real click drills.
    const clock = { t: 1000 };
    const spy = vi.spyOn(performance, "now").mockImplementation(() => clock.t);
    try {
      const onCommit = vi.fn();
      const svgRef = { current: fakeSvg() };
      const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));
      act(() => result.current.begin("segment:s", { x: 0, y: 0 }, ptr({ clientX: 0, clientY: 0 })));
      act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 40, clientY: 40 })));
      act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 40, clientY: 40 }))); // commit @1000
      expect(onCommit).toHaveBeenCalledTimes(1);
      // No consumeDragClick for the drag's own click (it went to the <svg>).
      clock.t = 1300; // 300 ms later, past the 250 ms window
      expect(result.current.consumeDragClick("segment:s")).toBe(false); // drills, NOT swallowed
    } finally {
      spy.mockRestore();
    }
  });

  it("still swallows the drag's OWN trailing click within the window (S1)", () => {
    const clock = { t: 5000 };
    const spy = vi.spyOn(performance, "now").mockImplementation(() => clock.t);
    try {
      const onCommit = vi.fn();
      const svgRef = { current: fakeSvg() };
      const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));
      act(() => result.current.begin("segment:s", { x: 0, y: 0 }, ptr({ clientX: 0, clientY: 0 })));
      act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 40, clientY: 40 })));
      act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 40, clientY: 40 }))); // commit @5000
      clock.t = 5100; // 100 ms later, inside the window
      expect(result.current.consumeDragClick("segment:s")).toBe(true); // swallowed once
      expect(result.current.consumeDragClick("segment:s")).toBe(false); // cleared after consume
    } finally {
      spy.mockRestore();
    }
  });

  // --- S2: the delta uses the scale captured at begin, immune to a mid-drag CTM change ---
  it("uses the begin-captured scale, so a viewBox/CTM change mid-drag does not accelerate the tile (S2)", () => {
    let ctmScale = 1; // layout->screen; 1x at begin
    const svgRef = {
      current: {
        getScreenCTM: () => ({ a: ctmScale, d: ctmScale }),
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
        viewBox: { baseVal: { x: 0, y: 0, width: 100, height: 100 } },
        setPointerCapture: () => {},
        releasePointerCapture: () => {},
      } as unknown as SVGSVGElement,
    };
    const onCommit = vi.fn();
    const { result } = renderHook(() => usePinDrag({ svgRef, savedPins: {}, onCommit }));
    // begin at scale 1 -> captured sx = 1/1 = 1.
    act(() => result.current.begin("segment:s", { x: 100, y: 100 }, ptr({ clientX: 10, clientY: 10 })));
    // The viewBox grows mid-drag and the live CTM halves. WITHOUT the fix,
    // re-reading it would double the delta (+40); WITH the captured scale it
    // stays +20 (1:1), so the tile tracks the cursor instead of accelerating.
    ctmScale = 0.5;
    act(() => result.current.svgProps.onPointerMove(ptr({ clientX: 30, clientY: 30 })));
    expect(result.current.effectivePins["segment:s"]).toEqual({ x: 120, y: 120 });
    act(() => result.current.svgProps.onPointerUp(ptr({ clientX: 30, clientY: 30 })));
    expect(onCommit).toHaveBeenCalledWith(
      { "segment:s": { x: 120, y: 120 } },
      { key: "segment:s", at: { x: 120, y: 120 } },
    );
  });
});

describe("Level0 pin affordance -- hosts are never pinnable (d, UI half)", () => {
  function renderLevel0(props: Partial<Parameters<typeof Level0>[0]> = {}) {
    return render(
      <Level0
        graph={graph}
        filters={NO_FILTERS}
        go={() => {}}
        onFocus={() => {}}
        savedPins={{}}
        onSavePins={() => {}}
        {...props}
      />,
    );
  }

  it("exposes a pin handle ONLY on segment tiles and site columns", () => {
    const { container } = renderLevel0();
    const keys = [...container.querySelectorAll("[data-pinnable]")].map(
      (el) => el.getAttribute("data-pinnable") ?? "",
    );
    // Every pinnable key is `site:` or `segment:` -- the server's own accepted
    // set (`console_files.py` `_PIN_PREFIXES`); never a host shape.
    for (const key of keys) {
      expect(key.startsWith("site:") || key.startsWith("segment:"), key).toBe(true);
      expect(
        key.startsWith("host:") ||
          key.startsWith("address:") ||
          key.startsWith("hostname:") ||
          key.startsWith("mac:"),
        key,
      ).toBe(false);
    }
    // 6 tiles + 3 sites; the 3 infra devices (which ARE hosts) carry none.
    expect(keys.filter((k) => k.startsWith("segment:"))).toHaveLength(6);
    expect(keys.filter((k) => k.startsWith("site:"))).toHaveLength(3);
    const devices = [...container.querySelectorAll("rect.device")];
    expect(devices).toHaveLength(3);
    for (const dev of devices) {
      expect(dev.closest("[data-pinnable]")).toBeNull();
    }
  });

  it("keeps drilling on a plain tile click (the pin affordance does not swallow it)", () => {
    const go = vi.fn();
    render(
      <Level0 graph={graph} filters={NO_FILTERS} go={go} onFocus={() => {}} savedPins={{}} onSavePins={() => {}} />,
    );
    fireEvent.click(screen.getByLabelText(/Segment 10\.80\.20\.0\/24/));
    expect(go).toHaveBeenCalledTimes(1);
  });
});

describe("Level0 reset control (b, UI half)", () => {
  it("appears only when pins exist and persists an empty set on click", () => {
    const onSavePins = vi.fn();
    const { queryByTestId, rerender } = render(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} savedPins={{}} onSavePins={onSavePins} />,
    );
    expect(queryByTestId("pin-reset")).toBeNull(); // no pins yet

    rerender(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} savedPins={{ "site:10.80": { x: 5, y: 5 } }} onSavePins={onSavePins} />,
    );
    const reset = queryByTestId("pin-reset");
    expect(reset).not.toBeNull();
    fireEvent.click(reset!);
    expect(onSavePins).toHaveBeenCalledWith({});
  });

  it("hides the pin controls entirely on a read-only render (no writer)", () => {
    const { queryByTestId } = render(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />,
    );
    expect(queryByTestId("pin-controls")).toBeNull();
  });
});

describe("Level0 viewBox grows to contain a far pin (no clip)", () => {
  it("is the pure canvas with no pins, and expands when a pin pushes a tile out", () => {
    const pure = layout0(graph);
    const noPins = render(
      <Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} savedPins={{}} onSavePins={() => {}} />,
    );
    // With no pins the viewBox is exactly the pure canvas (S3 unchanged).
    expect(noPins.getByTestId("level0-svg").getAttribute("viewBox")).toBe(`0 0 ${pure.width} ${pure.height}`);
    noPins.unmount();

    // A pin far to the right/below grows the viewBox so the tile stays in view.
    const far = render(
      <Level0
        graph={graph}
        filters={NO_FILTERS}
        go={() => {}}
        onFocus={() => {}}
        savedPins={{ "segment:10.80.20.0/24": { x: pure.width + 500, y: pure.height + 400 } }}
        onSavePins={() => {}}
      />,
    );
    const box = far.getByTestId("level0-svg").getAttribute("viewBox")!.split(" ").map(Number);
    expect(box[2]).toBeGreaterThan(pure.width); // width grew
    expect(box[3]).toBeGreaterThan(pure.height); // height grew
  });

  it("grows for a PARKED added tile, which sits outside the pure canvas by design (S5.1.d, review C)", () => {
    // An unplaced added segment whose column has no room is parked to the right
    // of every drawn box -- deliberately past `l0.width`. Its whole visibility
    // therefore rests on the view growing the viewBox for it, and nothing
    // asserted that: the far-pin case above covers a PIN, and a parked tile has
    // no pin at all.
    const pure = layout0(graph);
    const added = [
      { cidr: "10.80.201.0/24", name: "lab-a", state: "pending" as const, cloneOf: null, hostCount: 0, fallback: null },
    ];
    const view = render(
      <Level0
        graph={graph}
        filters={NO_FILTERS}
        go={() => {}}
        onFocus={() => {}}
        savedPins={{}}
        onSavePins={() => {}}
        added={added}
      />,
    );
    const tile = view.container.querySelector('[data-testid="added-tile"][data-cidr="10.80.201.0/24"]')!;
    const right = Number(tile.getAttribute("x")) + Number(tile.getAttribute("width"));
    // The case this guards: it really is outside the pure canvas.
    expect(right).toBeGreaterThan(pure.width);
    const box = view.getByTestId("level0-svg").getAttribute("viewBox")!.split(" ").map(Number);
    expect(box[2]).toBeGreaterThanOrEqual(right);
  });
});

describe("a drag suppresses TEXT SELECTION for its own duration only", () => {
  /**
   * jsdom performs no text selection, so the real effect -- 364 characters
   * swept across tile labels by one drag in Chromium -- cannot be shown here.
   * What is pinned is the MECHANISM: a `selectstart` (the event whose default
   * action begins a selection) is cancelled while a gesture is active and only
   * then, and the press itself is left alone, so focus-on-press still works.
   */
  function selectStartAllowed(target: Element): boolean {
    // `dispatchEvent` returns false exactly when a listener called preventDefault.
    return target.dispatchEvent(new Event("selectstart", { bubbles: true, cancelable: true }));
  }

  function draw(props: Partial<Parameters<typeof Level0>[0]> = {}) {
    return render(
      <Level0
        graph={graph}
        filters={NO_FILTERS}
        go={() => {}}
        onFocus={() => {}}
        savedPins={{}}
        onSavePins={() => {}}
        onCloneDrop={() => {}}
        {...props}
      />,
    );
  }

  it("a PIN drag: allowed before, cancelled from the press to the release, allowed after -- the press itself not cancelled", () => {
    const { container } = draw();
    const svg = screen.getByTestId("level0-svg");
    const handle = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    const label = handle.querySelector("text")!;

    expect(selectStartAllowed(label)).toBe(true);
    // `fireEvent` returns false when the default was prevented: the press is NOT.
    expect(fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientX: 0, clientY: 0 })).toBe(true);
    expect(selectStartAllowed(label)).toBe(false);
    fireEvent.pointerMove(svg, { pointerId: 1, clientX: 40, clientY: 30 });
    expect(selectStartAllowed(label)).toBe(false);
    fireEvent.pointerUp(svg, { pointerId: 1, clientX: 40, clientY: 30 });
    expect(selectStartAllowed(label)).toBe(true);
  });

  it("an ALT-drag clone: the same scope, and a cancelled pointer ends it too", () => {
    const { container } = draw();
    const svg = screen.getByTestId("level0-svg");
    const handle = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    const label = handle.querySelector("text")!;

    expect(fireEvent.pointerDown(handle, { button: 0, pointerId: 2, clientX: 0, clientY: 0, altKey: true })).toBe(true);
    fireEvent.pointerMove(svg, { pointerId: 2, clientX: 60, clientY: 10 });
    expect(screen.getByTestId("clone-ghost")).toBeTruthy();
    expect(selectStartAllowed(label)).toBe(false);
    fireEvent.pointerCancel(svg, { pointerId: 2, clientX: 60, clientY: 10 });
    expect(selectStartAllowed(label)).toBe(true);
  });

  it("outside a gesture nothing is cancelled: a press on a non-pinnable, text elsewhere, and a read-only map", () => {
    const { unmount } = draw();
    // The Internet pill is a control, not a drag handle.
    const pill = screen.getByLabelText(/^Internet:/);
    fireEvent.pointerDown(pill, { button: 0, pointerId: 3, clientX: 0, clientY: 0 });
    expect(selectStartAllowed(pill.querySelector("text")!)).toBe(true);
    // Text in the notes above the plane stays selectable.
    expect(selectStartAllowed(screen.getByTestId("budget-note"))).toBe(true);
    unmount();

    // With no writer no gesture begins, so even a press on a tile cancels nothing.
    const readOnly = render(<Level0 graph={graph} filters={NO_FILTERS} go={() => {}} onFocus={() => {}} />);
    const handle = readOnly.container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    fireEvent.pointerDown(handle, { button: 0, pointerId: 4, clientX: 0, clientY: 0 });
    expect(selectStartAllowed(handle.querySelector("text")!)).toBe(true);
  });

  it("unmounting the map removes the listener: a later selection on the page is never cancelled by a stale gesture", () => {
    const { container, unmount } = draw();
    const handle = container.querySelector('[data-pinnable="segment:10.80.20.0/24"]')!;
    // A press with no release (the map unmounts mid-gesture, e.g. a drill).
    fireEvent.pointerDown(handle, { button: 0, pointerId: 5, clientX: 0, clientY: 0 });
    unmount();
    const elsewhere = document.createElement("p");
    document.body.appendChild(elsewhere);
    try {
      expect(selectStartAllowed(elsewhere)).toBe(true);
    } finally {
      elsewhere.remove();
    }
  });
});
