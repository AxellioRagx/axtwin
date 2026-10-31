import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { getWriteToken, onWriteTokenChange, setWriteToken } from "../../api/client";
import { CanvasMenu } from "../canvas/CanvasMenu";

/** The header: the areas (Discovery with its Sources and Runs beneath it, Imprints,
 *  Twins, Canvas with its designs listed in a drop-down and Designs / New design
 *  beneath it), and the write token for this tab (never persisted). */
export function AppFrame() {
  const [token, setToken] = useState(getWriteToken() ?? "");
  useEffect(() => onWriteTokenChange(() => setToken(getWriteToken() ?? "")), []);
  const { pathname } = useLocation();
  const inDiscovery = pathname === "/discovery" || pathname.startsWith("/discovery/");
  const inCanvas = pathname === "/canvas" || pathname.startsWith("/canvas/") || /^\/twins\/[^/]+\/canvas$/.test(pathname);
  const openDesign = /^\/canvas\/([^/]+)$/.exec(pathname)?.[1] ?? /^\/twins\/([^/]+)\/canvas$/.exec(pathname)?.[1] ?? null;
  const designId = openDesign && openDesign !== "new" ? decodeURIComponent(openDesign) : null;
  return (
    <div className="frame">
      <header className="topbar">
        <span className="brand">axTwin</span>
        <nav aria-label="Areas">
          <NavLink to="/discovery" className={() => (inDiscovery ? "active" : "")}>
            Discovery
          </NavLink>
          <NavLink to="/imprints">Imprints</NavLink>
          <NavLink to="/twins">Twins</NavLink>
          <CanvasMenu active={inCanvas} />
        </nav>
        <span className="spacer" />
        <label className="token">
          Write token
          <input
            type="password"
            aria-label="Write token"
            placeholder="required for writes"
            value={token}
            onChange={(e) => setWriteToken(e.target.value)}
            autoComplete="off"
          />
        </label>
      </header>
      {inDiscovery ? (
        <nav className="subbar" aria-label="Discovery">
          <NavLink to="/discovery" className={() => (pathname.startsWith("/discovery/runs") ? "" : "active")}>
            Sources
          </NavLink>
          <NavLink to="/discovery/runs">Runs</NavLink>
        </nav>
      ) : null}
      {inCanvas ? (
        <nav className="subbar" aria-label="Canvas">
          <NavLink to="/canvas" end>
            Designs
          </NavLink>
          <NavLink to="/canvas/new">New design</NavLink>
          {designId ? (
            <NavLink to={`/canvas/${encodeURIComponent(designId)}`} className="active">
              {designId}
            </NavLink>
          ) : null}
        </nav>
      ) : null}
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
