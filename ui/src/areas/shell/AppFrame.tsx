import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { getWriteToken, onWriteTokenChange, setWriteToken } from "../../api/client";

/** The header: the areas (Discovery with its Sources and Runs beneath it, Imprints),
 *  and the write token for this tab (never persisted). */
export function AppFrame() {
  const [token, setToken] = useState(getWriteToken() ?? "");
  useEffect(() => onWriteTokenChange(() => setToken(getWriteToken() ?? "")), []);
  const { pathname } = useLocation();
  const inDiscovery = pathname === "/discovery" || pathname.startsWith("/discovery/");
  return (
    <div className="frame">
      <header className="topbar">
        <span className="brand">axRange</span>
        <nav aria-label="Areas">
          <NavLink to="/discovery" className={() => (inDiscovery ? "active" : "")}>
            Discovery
          </NavLink>
          <NavLink to="/imprints">Imprints</NavLink>
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
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
