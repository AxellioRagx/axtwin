import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";

import { useApi } from "../../api/context";
import { UNKNOWN_WHOAMI, WhoamiContext, signinHref } from "../../api/whoami";
import { BrandMark } from "./BrandMark";
import { TopbarSlotContext } from "./topbar";
import { useLoad } from "./useLoad";

/** The header: XTwin, the home link -- the blank, untitled canvas at `/` -- then the areas
 *  (Discovery with Sources, Runs and Imprints beneath it, Twins, Targets), and who the
 *  operator is under the API's write policy (A8). An existing twin opens on the canvas
 *  through the canvas bar's Open… (or a twin's Canvas link). The
 *  console holds no credential: `GET /api/whoami` on load names the caller and
 *  whether writes will be accepted; a write that is refused anyway says so.
 *
 *  Two kinds of route (ui/Spec.md "Shell"): list and detail pages live in the
 *  centred `.main` column under the bar and a sub-bar; a canvas route
 *  (`/`, `/canvas/new`, `/canvas/:id`, `/twins/:id/canvas`) is the page -- it fills the window
 *  under the one bar and renders its title, pills and exits INTO the bar
 *  through `TopbarPortal`. */
export function AppFrame() {
  const api = useApi();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  // An older server without the route answers 404: the console then assumes
  // nothing (UNKNOWN_WHOAMI) and lets each write find out.
  const who = useLoad((signal) => api.whoami(signal).catch(() => UNKNOWN_WHOAMI), []);
  const whoami = who.data ?? UNKNOWN_WHOAMI;
  const { pathname } = useLocation();
  const inImprints = pathname === "/imprints" || pathname.startsWith("/imprints/");
  const inDiscovery = pathname === "/discovery" || pathname.startsWith("/discovery/") || inImprints;
  // `/` and `/canvas/new` are the untitled design; `/canvas/<id>` and `/twins/<id>/canvas` a named one.
  const isCanvas = pathname === "/" || /^\/canvas\/[^/]+$/.test(pathname) || /^\/twins\/[^/]+\/canvas$/.test(pathname);
  const signin = signinHref(whoami.signin_url);
  return (
    <WhoamiContext.Provider value={whoami}>
      <div className={`frame${isCanvas ? " canvas-frame" : ""}`}>
        <header className="topbar">
          <Link to="/" className="brand" aria-label="XTwin: a new design on the canvas" title="XTwin: a new design on the canvas">
            <BrandMark />
            <span>Twin</span>
          </Link>
          <nav aria-label="Areas">
            <NavLink to="/discovery" className={() => (inDiscovery ? "active" : "")}>
              Discovery
            </NavLink>
            <NavLink to="/twins">Twins</NavLink>
            <NavLink to="/targets">Targets</NavLink>
          </nav>
          <div className="topbar-slot" ref={setSlot} />
          {who.data ? (
            <span className="who" data-testid="whoami" title={whoami.mode ? `auth mode: ${whoami.mode}` : "the running API reports no auth mode"}>
              {whoami.user ? <span className="who-name">{whoami.user}</span> : null}
              {whoami.can_write ? null : (
                <span className="badge read-only">read-only</span>
              )}
              {!whoami.can_write && signin ? (
                <a href={signin} className="who-signin">
                  Sign in
                </a>
              ) : null}
            </span>
          ) : null}
        </header>
        {inDiscovery ? (
          <nav className="subbar" aria-label="Discovery">
            <NavLink to="/discovery" className={() => (pathname.startsWith("/discovery/runs") || inImprints ? "" : "active")}>
              Sources
            </NavLink>
            <NavLink to="/discovery/runs">Runs</NavLink>
            <NavLink to="/imprints">Imprints</NavLink>
          </nav>
        ) : null}
        <TopbarSlotContext.Provider value={slot}>
          {isCanvas ? (
            <main className="canvas-main">
              <Outlet />
            </main>
          ) : (
            <main className="main">
              <Outlet />
            </main>
          )}
        </TopbarSlotContext.Provider>
      </div>
    </WhoamiContext.Provider>
  );
}
