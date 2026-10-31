import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router";

import { useApi } from "../../api/context";
import { errorText } from "../shell/parts";
import { useLoad } from "../shell/useLoad";

/**
 * The Canvas entry of the top menu: the area link, and beside it a drop-down
 * that lists every twin in the catalog -- cast from an imprint or authored --
 * to open on the canvas, and the way to a new design. The list is read when
 * the menu opens, so the header costs nothing while it stays closed.
 */
export function CanvasMenu({ active }: { active: boolean }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="area-menu" ref={root}>
      <NavLink to="/canvas" className={() => (active ? "active" : "")}>
        Canvas
      </NavLink>
      <button type="button" className="caret" aria-label="Open a design" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        ▾
      </button>
      {open ? <DesignList close={() => setOpen(false)} /> : null}
    </div>
  );
}

function DesignList({ close }: { close: () => void }) {
  const api = useApi();
  const twins = useLoad((signal) => api.listTwins(signal), []);
  const rows = twins.data?.imprints ?? [];
  return (
    <ul role="menu" aria-label="Designs" className="dropdown">
      <li role="none">
        <Link role="menuitem" to="/canvas/new" onClick={close}>
          New design…
        </Link>
      </li>
      <li role="separator" className="sep" />
      {twins.loading && !twins.data ? (
        <li role="none" className="muted note">
          Loading…
        </li>
      ) : null}
      {twins.error ? (
        <li role="none" className="muted note">
          twins not listed: {errorText(twins.error)}
        </li>
      ) : null}
      {twins.data && rows.length === 0 ? (
        <li role="none" className="muted note">
          No twin in the catalog yet.
        </li>
      ) : null}
      {rows.map((t) => (
        <li role="none" key={t.id}>
          <Link role="menuitem" to={`/canvas/${encodeURIComponent(t.id)}`} onClick={close}>
            <span>{t.id}</span>
            <span className="muted">{t.cast_from ? `cast from ${t.cast_from.pit_name ?? t.cast_from.pit_id}` : "authored"}</span>
          </Link>
        </li>
      ))}
      <li role="separator" className="sep" />
      <li role="none">
        <Link role="menuitem" to="/canvas" onClick={close}>
          All designs
        </Link>
      </li>
    </ul>
  );
}
