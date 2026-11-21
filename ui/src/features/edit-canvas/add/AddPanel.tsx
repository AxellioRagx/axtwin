/**
 * The Add panel (`docs/design/canvas-add-panel.md` §3), in the palette's slot (the rail's `+`):
 * one search over devices, roles and tools; the five device cards (§4); the tools (§6); the recent
 * and favourite items this browser keeps; and the design-health line (hints only, never a Check).
 * Every card can be dragged onto the canvas or clicked (the selected segment, else the view's
 * centre); what it writes is chosen in the drop popover. Nothing here names a persona or an image:
 * roles are read from the API's persona library, images from its image profiles.
 */
import { useMemo, useState, type ReactNode } from "react";

import type { Persona, Template } from "../../../api/types";
import type { PaletteItem } from "../edit/EditContext";
import { PALETTE_MIME, useAddTarget } from "./AddContext";
import { DEVICE_CARDS, FAVOURITES_KEY, RECENT_KEY, TOOLS, readRefs, refKey, searchAdd, toggleFavourite, writeRefs, type AddHit, type AddRef, type CardId, type ToolId } from "./catalog";

export interface AddPanelProps {
  personas: readonly Persona[];
  /** The templates the search covers too (their cards are the `templates` section). */
  templateList?: readonly Template[];
  /** The design-health hints (`catalog.ts` `healthHints`). */
  health: readonly string[];
  /** One-click fixes the hints offer (B1: "Remove 2 dangling links"). */
  fixes?: ReadonlyArray<{ label: string; run(): void }>;
  disabled: boolean;
  note: string | null;
  /** The Templates section (phase 2), when there is one. */
  templates?: ReactNode;
}

export function itemOfRef(r: AddRef): PaletteItem {
  if (r.kind === "device") return { kind: "device", id: r.card, card: r.card, role: r.role, os: r.os, label: r.label };
  if (r.kind === "tool") return { kind: "tool", id: r.tool, tool: r.tool, label: r.label };
  return { kind: "template", id: r.template, label: r.label };
}

export function refOfItem(i: PaletteItem): AddRef | null {
  if (i.kind === "device" && i.card) return { kind: "device", card: i.card, role: i.role ?? null, os: i.os ?? null, label: i.label };
  if (i.kind === "tool" && i.tool) return { kind: "tool", tool: i.tool, label: i.label };
  if (i.kind === "template") return { kind: "template", template: i.id, label: i.label };
  return null;
}

export function itemOfHit(h: AddHit): PaletteItem {
  if (h.kind === "device") return { kind: "device", id: h.card, card: h.card, role: h.role, os: h.os, label: h.label };
  if (h.kind === "template") return { kind: "template", id: h.template, label: h.label };
  return { kind: "tool", id: h.tool, tool: h.tool, label: h.label };
}

export const cardItem = (card: CardId): PaletteItem => ({ kind: "device", id: card, card, label: DEVICE_CARDS.find((c) => c.id === card)!.label });
export const toolItem = (tool: ToolId): PaletteItem => ({ kind: "tool", id: tool, tool, label: TOOLS.find((t) => t.id === tool)!.label });

/** One draggable, clickable entry of the panel. */
export function AddEntry({ item, detail, disabled, testId, favourite, onFavourite, onPick, media }: { item: PaletteItem; detail?: string; disabled: boolean; testId: string; favourite?: boolean; onFavourite?: () => void; onPick?: (item: PaletteItem) => void; media?: ReactNode }) {
  const target = useAddTarget();
  const pick = onPick ?? ((i: PaletteItem) => target?.addHere(i));
  return (
    <li
      className={`cv-add-entry${disabled ? " disabled" : ""}${media ? " has-media" : ""}`}
      draggable={!disabled}
      data-testid={testId}
      data-kind={item.kind}
      data-id={item.id}
      data-role={item.role ?? undefined}
      title={detail ?? item.label}
      onDragStart={(e) => {
        e.dataTransfer.setData(PALETTE_MIME, JSON.stringify(item));
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={() => {
        if (!disabled) pick(item);
      }}
    >
      {media ? <span className="cv-add-media">{media}</span> : null}
      <span className="cv-add-label">{item.label}</span>
      {detail ? <span className="cv-add-detail muted">{detail}</span> : null}
      {onFavourite ? (
        <button
          type="button"
          className={`cv-add-star${favourite ? " on" : ""}`}
          aria-label={favourite ? `Unpin ${item.label}` : `Pin ${item.label}`}
          aria-pressed={favourite}
          onClick={(e) => {
            e.stopPropagation();
            onFavourite();
          }}
        >
          {favourite ? "★" : "☆"}
        </button>
      ) : null}
    </li>
  );
}

/** The search's hit list, shared by the panel and the double-click quick-add. */
export function AddHits({ hits, disabled, onPick }: { hits: readonly AddHit[]; disabled: boolean; onPick?: (item: PaletteItem) => void }) {
  if (!hits.length) return <p className="muted cv-palette-hint">nothing matches</p>;
  return (
    <ul className="cv-add-list" aria-label="Search results">
      {hits.slice(0, 12).map((h) => {
        const item = itemOfHit(h);
        return <AddEntry key={`${h.kind}:${h.kind === "device" ? `${h.card}:${h.role ?? ""}` : h.kind === "template" ? h.template : h.tool}`} item={item} detail={h.detail} disabled={disabled} testId="add-hit" {...(onPick ? { onPick } : {})} />;
      })}
    </ul>
  );
}

export function AddPanel({ personas, templateList = [], health, fixes = [], disabled, note, templates }: AddPanelProps) {
  const target = useAddTarget();
  const [query, setQuery] = useState("");
  const [favourites, setFavourites] = useState<AddRef[]>(() => readRefs(FAVOURITES_KEY));
  // Read when the panel opens (it mounts with the popover); a drop's commit writes it.
  const [recent] = useState<AddRef[]>(() => readRefs(RECENT_KEY));
  const hits = useMemo(() => searchAdd(query, personas, templateList), [query, personas, templateList]);
  const favKeys = new Set(favourites.map(refKey));
  const toggle = (item: PaletteItem) => {
    const ref = refOfItem(item);
    if (!ref) return;
    const next = toggleFavourite(favourites, ref);
    setFavourites(next);
    writeRefs(FAVOURITES_KEY, next);
  };
  const pinned = [...favourites, ...recent.filter((r) => !favKeys.has(refKey(r)))];

  return (
    <aside className="cv-palette cv-add" aria-label="Add panel" data-testid="palette">
      <div className="cv-palette-head">
        <input
          type="search"
          aria-label="Search the Add panel"
          placeholder="search: file server, AD, plc, office…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && hits[0] && !disabled) target?.addHere(itemOfHit(hits[0]));
          }}
        />
      </div>
      {disabled ? <p className="muted cv-palette-hint" data-testid="palette-hint">view mode: the Add panel is read-only</p> : null}
      {note ? <p className="muted cv-palette-hint">{note}</p> : null}
      {query.trim() ? (
        <AddHits hits={hits} disabled={disabled} />
      ) : (
        <>
          <section className="cv-palette-cat" aria-label="Devices" data-testid="add-devices">
            <h4 className="cv-add-head">Devices</h4>
            <ul className="cv-add-list">
              {DEVICE_CARDS.map((c) => {
                const item = cardItem(c.id);
                return <AddEntry key={c.id} item={item} detail={c.line} disabled={disabled} testId="add-card" favourite={favKeys.has(refKey(refOfItem(item)!))} onFavourite={() => toggle(item)} />;
              })}
            </ul>
          </section>
          {templates}
          <section className="cv-palette-cat" aria-label="Tools" data-testid="add-tools">
            <h4 className="cv-add-head">Tools</h4>
            <ul className="cv-add-list">
              {TOOLS.map((t) => {
                const item = toolItem(t.id);
                return <AddEntry key={t.id} item={item} detail={t.line} disabled={disabled} testId="add-tool" favourite={favKeys.has(refKey(refOfItem(item)!))} onFavourite={() => toggle(item)} />;
              })}
            </ul>
          </section>
          <section className="cv-palette-cat" aria-label="Recent and favourites" data-testid="add-recent">
            <h4 className="cv-add-head">Recent and favourites</h4>
            {pinned.length ? (
              <ul className="cv-add-list">
                {pinned.map((r) => {
                  const item = itemOfRef(r);
                  return <AddEntry key={refKey(r)} item={item} disabled={disabled} testId="add-recent-item" favourite={favKeys.has(refKey(r))} onFavourite={() => toggle(item)} />;
                })}
              </ul>
            ) : (
              <p className="muted cv-palette-hint">nothing yet: what you add and pin shows here</p>
            )}
          </section>
        </>
      )}
      <p className="muted cv-add-health" data-testid="add-health">
        {health.length ? health.join(" · ") : "no design hints"}
        {fixes.map((f) => (
          <button key={f.label} type="button" className="link" disabled={disabled} data-testid="add-health-fix" onClick={f.run}>
            {f.label}
          </button>
        ))}
      </p>
    </aside>
  );
}
