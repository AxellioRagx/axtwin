/**
 * The palette (`SPEC.md` "Palette & placement"): a searchable, categorised
 * list of what can be placed -- the images and node groups the catalog's
 * twin-configs declare (this twin's own first; other twins' under a folded
 * "From other twins"), the personas the API lists, and a segment -- with a
 * count field for N copies named `{name}-{n}`. ONE placement rule (the GNS3
 * pattern, 2026-11-06): a click ARMS the item, the next click on a segment
 * places it there (a router: a segment for its first leg, or an empty spot
 * for none); Esc disarms. Dragging an item onto a segment places it too. A
 * segment is added at once; a persona binds to the selected node group at
 * once. Nothing here names an image or persona: every entry is read from
 * the API.
 */
import { useMemo, useState } from "react";

import type { PaletteItem } from "./edit/EditContext";

export interface PaletteProps {
  items: PaletteItem[];
  /** The armed item for click-to-place, or null. */
  placing: PaletteItem | null;
  onArm(item: PaletteItem | null): void;
  /** Place an item that needs no target now: a segment, or a persona onto the selected node group. */
  onPlace(item: PaletteItem, count: number): void;
  count: number;
  onCount(n: number): void;
  /** Something is selected that a persona can bind to. */
  hasNodeSelection: boolean;
  disabled: boolean;
  note: string | null;
}

export const PALETTE_MIME = "application/x-axr-palette";

/** The palette's categories: a kind, except that another twin's groups go
 *  under their own folded heading so a new design is not offered 500 names
 *  from every twin in the catalog. */
type Category = PaletteItem["kind"] | "foreign";
const CATEGORY_LABEL: Record<Category, string> = { segment: "Segments", router: "Routers", group: "Node groups", image: "Images", persona: "Personas", foreign: "From other twins" };
const CATEGORY_ORDER: Category[] = ["segment", "router", "group", "image", "persona", "foreign"];

function categoryOf(item: PaletteItem): Category {
  return item.kind === "group" && item.foreign ? "foreign" : item.kind;
}

/** What needs no click on a segment after arming: a segment (added at once), a
 *  persona (bound to the selection at once), a router (a segment for its
 *  first leg OR an empty spot). */
export function placesWithoutSegment(item: PaletteItem): boolean {
  return item.kind === "segment" || item.kind === "persona" || item.kind === "router" || item.nodeKind === "router";
}

/** Armed by a click: everything but a segment and a persona, which act at once. */
export function armsOnClick(item: PaletteItem): boolean {
  return item.kind !== "segment" && item.kind !== "persona";
}

export function Palette({ items, placing, onArm, onPlace, count, onCount, hasNodeSelection, disabled, note }: PaletteProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({ segment: true, router: true, group: true, image: true, persona: false, foreign: false });
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? items.filter((i) => `${i.label} ${i.id} ${i.role ?? ""} ${i.summary ?? ""} ${i.os ?? ""}`.toLowerCase().includes(q)) : items;
    const by = new Map<Category, PaletteItem[]>();
    for (const i of list) by.set(categoryOf(i), [...(by.get(categoryOf(i)) ?? []), i]);
    return CATEGORY_ORDER.filter((k) => by.has(k)).map((k) => ({ kind: k, items: by.get(k)!, searched: Boolean(q) }));
  }, [items, query]);
  const hint = disabled
    ? "view mode: the palette is read-only"
    : placing
      ? `armed: ${placing.label} — click a segment to place it${placesWithoutSegment(placing) ? ", or an empty spot for no leg yet" : ""}; Esc disarms`
      : "click an item to arm it, then click a segment (a router: a segment or an empty spot); or drag it onto a segment. A segment is added at once; a persona binds to the selected node group.";

  return (
    <aside className="cv-palette" aria-label="Palette" data-testid="palette">
      <div className="cv-palette-head">
        <input type="search" aria-label="Search the palette" placeholder="search images, groups, personas" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="cv-count">
          copies
          <input type="number" min={1} max={999} aria-label="Copies" value={count} onChange={(e) => onCount(Math.max(1, Math.min(999, Number(e.target.value) || 1)))} />
        </label>
      </div>
      <p className="muted cv-palette-hint" data-testid="palette-hint">
        {hint}
      </p>
      {note ? <p className="muted cv-palette-hint">{note}</p> : null}
      {shown.length === 0 ? <p className="muted cv-palette-hint">{items.length ? "nothing matches" : "the API lists nothing to place"}</p> : null}
      {shown.map((cat) => (
        <section key={cat.kind} className="cv-palette-cat">
          <button type="button" className="cv-palette-cat-head" aria-expanded={open[cat.kind] !== false || cat.searched} onClick={() => setOpen((o) => ({ ...o, [cat.kind]: o[cat.kind] === false }))}>
            {CATEGORY_LABEL[cat.kind]} <span className="muted">({cat.items.length})</span>
          </button>
          {open[cat.kind] !== false || cat.searched ? (
            <ul className="cv-palette-list">
              {cat.items.map((item) => (
                <li
                  key={`${item.kind}:${item.id}`}
                  className={`cv-palette-item${placing && placing.kind === item.kind && placing.id === item.id ? " armed" : ""}${item.available === false ? " unavailable" : ""}`}
                  draggable={!disabled}
                  data-testid="palette-item"
                  data-kind={item.kind}
                  data-id={item.id}
                  title={item.summary ?? item.id}
                  onDragStart={(e) => {
                    e.dataTransfer.setData(PALETTE_MIME, JSON.stringify({ ...item, count }));
                    e.dataTransfer.effectAllowed = "copy";
                  }}
                  aria-pressed={placing ? placing.kind === item.kind && placing.id === item.id : undefined}
                  onClick={() => {
                    if (disabled) return;
                    if (!armsOnClick(item)) onPlace(item, count);
                    else onArm(placing && placing.id === item.id && placing.kind === item.kind ? null : { ...item, count });
                  }}
                >
                  <span className="cv-palette-label">{item.label}</span>
                  {item.kind === "image" ? <span className={`badge ${item.available === false ? "" : "ok"}`} title={item.available === false ? "no twin in the catalog uses this image" : "used by a twin in the catalog"}>{item.available === false ? "unlisted" : "in catalog"}</span> : null}
                  {item.kind === "group" ? <span className="badge">{item.role ?? "no role"}{item.count && item.count > 1 ? ` ×${item.count}` : ""}</span> : null}
                  {item.kind === "persona" ? <span className="badge" title={hasNodeSelection ? "binds to the selected node group" : "select a node group first"}>{item.os ?? "any OS"}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </aside>
  );
}
