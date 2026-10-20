import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createClient } from "./api/client";
import { App } from "./routes";
import "./theme/tokens.css";
import "./theme/shell.css";
import "./theme/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing #root");

// Same-origin by default (`axrange serve --spa-dir` co-serves this build
// beside `/api`); `VITE_AXRANGE_API` only exists for a dev server that is not
// proxying. No token is read here: `setWriteToken` supplies it at runtime.
createRoot(root).render(
  <StrictMode>
    <App client={createClient()} />
  </StrictMode>,
);
