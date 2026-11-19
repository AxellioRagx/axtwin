import { Navigate, Route, Routes, useLocation } from "react-router";

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
import { TargetsPage } from "./areas/targets/TargetsPage";
import { AppFrame } from "./areas/shell/AppFrame";
import { CanvasPage } from "./features/edit-canvas/CanvasPage";

/** The routes. The router itself is supplied by the caller (`BrowserRouter` in
 *  `main.tsx`, `MemoryRouter` in tests). */
export function App({ api }: { api: Api }) {
  return (
    <ApiContext.Provider value={api}>
      <Routes>
        <Route element={<AppFrame />}>
          <Route index element={<NewCanvas />} />
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
          <Route path="/targets" element={<TargetsPage />} />
          <Route path="/canvas" element={<Navigate to="/" replace />} />
          <Route path="/canvas/new" element={<NewCanvas />} />
          <Route path="/canvas/:id" element={<CanvasPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </ApiContext.Provider>
  );
}

/** `/` (home, XTwin's link) and `/canvas/new`: an untitled design on the canvas. Keyed by the location, so following "New design"
 *  while already on an untitled one (after the unsaved-work guard let it go) starts a fresh one, and
 *  a Save-As's move to `/canvas/<id>` mounts the named canvas afresh. */
function NewCanvas() {
  const { key } = useLocation();
  return <CanvasPage key={key} untitled />;
}
