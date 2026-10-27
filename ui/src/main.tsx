import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { makeRequester } from "./api/client";
import { makeApi } from "./api/discovery";
import { App } from "./App";
import "./theme/app.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing #root");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App api={makeApi(makeRequester())} />
    </BrowserRouter>
  </StrictMode>,
);
