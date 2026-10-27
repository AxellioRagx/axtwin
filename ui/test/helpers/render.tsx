import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { Api } from "../../src/api/discovery";
import { App } from "../../src/App";

export function renderAt(api: Api, path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App api={api} />
    </MemoryRouter>,
  );
}
