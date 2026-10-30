/**
 * The palette (`SPEC.md` "Palette & placement"): a searchable, categorised
 * list of what can be placed -- the images and node groups the catalog's
 * twin-configs declare, the personas the API lists, and a segment -- with a
 * count field for N copies named `{name}-{n}`. An item is dragged onto a
 * segment, or armed by a click and placed on the selected segment (Shift-
 * click places one copy at once). Nothing here names an image or persona:
 * every entry is read from the API.
 */
import { useMemo, useState } from "react";

import type { PaletteItem } from "./edit/EditContext";

export interface PaletteProps {
  items: PaletteItem[];
  /** The armed item for click-to-place, or null. */
  placing: PaletteItem | null;
  onArm(item: PaletteItem | null): void;
  /** Place the item now on the selected segment (Shift-click / the Place button). */
  onPlace(item: PaletteItem, count: number): void;
  count: number;
  onCount(n: number): void;
  selectedSegment: string | null;
  disabled: boolean;
  note: string | null;
}

export const PALETTE_MIME = "application/x-axr-palette";

const CATEGORY_LABEL: Record<PaletteItem["kind"], string> = { group: "Node groups", image: "Images", persona: "Personas", segment: "Segments" };
const CATEGORY_ORDER: PaletteItem["kind"][] = ["segment", "group", "image", "persona"];

export function Palette({ items, placing, onArm, onPlace, count, onCount, selectedSegment, disabled, note }: PaletteProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({ segment: true, group: true, image: true, persona: false });
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? items.filter((i) => `${i.label} ${i.id} ${i.role ?? ""} ${i.summary ?? ""} ${i.os ?? ""}`.toLowerCase().includes(q)) : items;
    const by = new Map<PaletteItem["kind"], PaletteItem[]>();
    for (const i of list) by.set(i.kind, [...(by.get(i.kind) ?? []), i]);
    return CATEGORY_ORDER.filter((k) => by.has(k)).map((k) => ({ kind: k, items: by.get(k)! }));
  }, [items, query]);

  return (
    <aside className="cv-palette" aria-label="Palette" data-testid="palette">
      <div className="cv-palette-head">
        <input type="search" aria-label="Search the palette" placeholder="search images, groups, personas" value={query} onChange={(e) => setQuery(e.target.value)} />
        <label className="cv-count">
          copies
          <input type="number" min={1} max={999} aria-label="Copies" value={count} onChange={(e) => onCount(Math.max(1, Math.min(999, Number(e.target.value) || 1)))} />
        </label>
      </div>
      <p className="muted cv-palette-hint">
        {disabled ? "view mode: the palette is read-only" : placing ? `armed: ${placing.label} — click a segment to place, Esc to disarm` : selectedSegment ? `drag onto a segment, or click to place on ${selectedSegment}` : "drag onto a segment, or select a segment and click an item"}
      </p>
      {note ? <p className="muted cv-palette-hint">{note}</p> : null}
      {shown.length === 0 ? <p className="muted cv-palette-hint">{items.length ? "nothing matches" : "the API lists nothing to place"}</p> : null}
      {shown.map((cat) => (
        <section key={cat.kind} className="cv-palette-cat">
          <button type="button" className="cv-palette-cat-head" aria-expanded={open[cat.kind] !== false} onClick={() => setOpen((o) => ({ ...o, [cat.kind]: o[cat.kind] === false }))}>
            {CATEGORY_LABEL[cat.kind]} <span className="muted">({cat.items.length})</span>
          </button>
          {open[cat.kind] !== false ? (
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
                  onClick={(e) => {
                    if (disabled) return;
                    if (e.shiftKey) onPlace(item, 1);
                    else if (selectedSegment || item.kind === "segment" || item.kind === "persona") onPlace(item, count);
                    else onArm(placing && placing.id === item.id && placing.kind === item.kind ? null : item);
                  }}
                >
                  <span className="cv-palette-label">{item.label}</span>
                  {item.kind === "image" ? <span className={`badge ${item.available === false ? "" : "ok"}`} title={item.available === false ? "no twin in the catalog uses this image" : "used by a twin in the catalog"}>{item.available === false ? "unlisted" : "in catalog"}</span> : null}
                  {item.kind === "group" ? <span className="badge">{item.role ?? "no role"}{item.count && item.count > 1 ? ` ×${item.count}` : ""}</span> : null}
                  {item.kind === "persona" ? <span className="badge">{item.os ?? "any OS"}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </aside>
  );
}
