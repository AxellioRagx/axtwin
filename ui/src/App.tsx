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
import { TwinPage } from "./areas/twins/TwinPage";
import { TwinsPage } from "./areas/twins/TwinsPage";
import { DesignsPage } from "./areas/canvas/DesignsPage";
import { NewDesignPage } from "./areas/canvas/NewDesignPage";
import { AppFrame } from "./areas/shell/AppFrame";
import { CanvasPage } from "./features/edit-canvas/CanvasPage";

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
          <Route path="/imprints/:pit" element={<ImprintPage />} />
          <Route path="/twins" element={<TwinsPage />} />
          <Route path="/twins/:id" element={<TwinPage />} />
          <Route path="/twins/:id/canvas" element={<CanvasPage />} />
          <Route path="/canvas" element={<DesignsPage />} />
          <Route path="/canvas/new" element={<NewDesignPage />} />
          <Route path="/canvas/:id" element={<CanvasPage />} />
          <Route path="*" element={<Navigate to="/discovery" replace />} />
        </Route>
      </Routes>
    </ApiContext.Provider>
  );
}
