// React Testing Library only auto-installs its cleanup when the runner exposes
// globals; this package runs vitest with `globals: false`, so wire it here.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

// jsdom lacks what React Flow measures with. Every element measures 0 here,
// so the canvas tests assert placement (the data attributes the nodes carry),
// never pixels.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
if (typeof globalThis.DOMMatrixReadOnly === "undefined") {
  class DOMMatrixReadOnlyStub {
    m22 = 1;
    constructor(_transform?: string) {}
  }
  globalThis.DOMMatrixReadOnly = DOMMatrixReadOnlyStub as unknown as typeof DOMMatrixReadOnly;
}
