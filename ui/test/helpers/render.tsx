import { StrictMode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { Api } from "../../src/api/discovery";
import { App } from "../../src/App";

/** The app at `path` over `api`. `strict`: inside `<StrictMode>`, as `main.tsx` renders it (mount,
 *  unmount, mount again in dev), for the hooks that must survive that. */
export function renderAt(api: Api, path: string, options: { strict?: boolean } = {}) {
  const app = (
    <MemoryRouter initialEntries={[path]}>
      <App api={api} />
    </MemoryRouter>
  );
  return render(options.strict ? <StrictMode>{app}</StrictMode> : app);
}
