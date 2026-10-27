// React Testing Library only auto-installs its cleanup when the runner exposes
// globals; this package runs vitest with `globals: false`, so wire it here.
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
