import { Navigate, Route, Routes } from "react-router";

import { ApiContext } from "./api/context";
import type { Api } from "./api/discovery";
import { RunPage } from "./areas/discovery/RunPage";
import { RunsPage } from "./areas/discovery/RunsPage";
import { SourceEditor } from "./areas/discovery/SourceEditor";
import { SourcePage } from "./areas/discovery/SourcePage";
import { SourcesPage } from "./areas/discovery/SourcesPage";
import { ImprintPage } from "./areas/imprints/ImprintPage";
import { ImprintsPage } from "./areas/imprints/ImprintsPage";
import { AppFrame } from "./areas/shell/AppFrame";

/** The routes. The router itself is supplied by the caller (`BrowserRouter` in
 *  `main.tsx`, `MemoryRouter` in tests). */
export function App({ api }: { api: Api }) {
  return (
    <ApiContext.Provider value={api}>
      <Routes>
        <Route element={<AppFrame />}>
          <Route index element={<Navigate to="/discovery" replace />} />
          <Route path="/discovery" element={<SourcesPage />} />
          <Route path="/discovery/sources/new" element={<SourceEditor />} />
          <Route path="/discovery/sources/:id" element={<SourcePage />} />
          <Route path="/discovery/sources/:id/edit" element={<SourceEditor />} />
          <Route path="/discovery/runs" element={<RunsPage />} />
          <Route path="/discovery/runs/:id" element={<RunPage />} />
          <Route path="/imprints" element={<ImprintsPage />} />
          <Route path="/imprints/:id" element={<ImprintPage />} />
          <Route path="*" element={<Navigate to="/discovery" replace />} />
        </Route>
      </Routes>
    </ApiContext.Provider>
  );
}
