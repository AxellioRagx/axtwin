import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { useApi } from "../../api/context";
import { UNKNOWN_WHOAMI, WhoamiContext, signinHref } from "../../api/whoami";
import { CanvasMenu } from "../canvas/CanvasMenu";
import { TopbarSlotContext } from "./topbar";
import { useLoad } from "./useLoad";

/** The header: the areas (Discovery with its Sources and Runs beneath it, Imprints,
 *  Twins, Targets, Canvas with its designs listed in a drop-down and Designs / New design
 *  beneath it), and who the operator is under the API's write policy (A8). The
 *  console holds no credential: `GET /api/whoami` on load names the caller and
 *  whether writes will be accepted; a write that is refused anyway says so.
 *
 *  Two kinds of route (ui/Spec.md "Shell"): list and detail pages live in the
 *  centred `.main` column under the bar and a sub-bar; a canvas route
 *  (`/canvas/:id`, `/twins/:id/canvas`) is the page -- it fills the window
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
  const inDiscovery = pathname === "/discovery" || pathname.startsWith("/discovery/");
  const inCanvas = pathname === "/canvas" || pathname.startsWith("/canvas/") || /^\/twins\/[^/]+\/canvas$/.test(pathname);
  const openDesign = /^\/canvas\/([^/]+)$/.exec(pathname)?.[1] ?? /^\/twins\/([^/]+)\/canvas$/.exec(pathname)?.[1] ?? null;
  const designId = openDesign && openDesign !== "new" ? decodeURIComponent(openDesign) : null;
  const isCanvas = designId !== null;
  const signin = signinHref(whoami.signin_url);
  return (
    <WhoamiContext.Provider value={whoami}>
      <div className={`frame${isCanvas ? " canvas-frame" : ""}`}>
        <header className="topbar">
          <span className="brand">axTwin</span>
          <nav aria-label="Areas">
            <NavLink to="/discovery" className={() => (inDiscovery ? "active" : "")}>
              Discovery
            </NavLink>
            <NavLink to="/imprints">Imprints</NavLink>
            <NavLink to="/twins">Twins</NavLink>
            <NavLink to="/targets">Targets</NavLink>
            <CanvasMenu active={inCanvas} />
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
            <NavLink to="/discovery" className={() => (pathname.startsWith("/discovery/runs") ? "" : "active")}>
              Sources
            </NavLink>
            <NavLink to="/discovery/runs">Runs</NavLink>
          </nav>
        ) : null}
        {inCanvas && !isCanvas ? (
          <nav className="subbar" aria-label="Canvas">
            <NavLink to="/canvas" end>
              Designs
            </NavLink>
            <NavLink to="/canvas/new">New design</NavLink>
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
