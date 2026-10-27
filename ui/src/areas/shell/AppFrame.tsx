import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { getWriteToken, onWriteTokenChange, setWriteToken } from "../../api/client";

/** The header: the areas, and the write token for this tab (never persisted). */
export function AppFrame() {
  const [token, setToken] = useState(getWriteToken() ?? "");
  useEffect(() => onWriteTokenChange(() => setToken(getWriteToken() ?? "")), []);
  return (
    <div className="frame">
      <header className="topbar">
        <span className="brand">axRange</span>
        <nav aria-label="Areas">
          <NavLink to="/discovery" end>
            Discovery sources
          </NavLink>
          <NavLink to="/discovery/runs">Runs</NavLink>
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
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
