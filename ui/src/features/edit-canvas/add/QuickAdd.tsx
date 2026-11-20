/**
 * The double-click quick-add (`docs/design/canvas-add-panel.md` §3): the Add panel's search as a
 * small popup at the pointer on the empty canvas. A pick drops the item there, through the same
 * drop rules and popover as a drag. Esc closes it.
 */
import { useMemo, useState } from "react";

import type { Persona } from "../../../api/types";
import type { PaletteItem } from "../edit/EditContext";
import { AddEntry, AddHits, cardItem, itemOfHit, toolItem } from "./AddPanel";
import { DEVICE_CARDS, TOOLS, searchAdd } from "./catalog";

export function QuickAdd({ personas, onPick, onClose }: { personas: readonly Persona[]; onPick(item: PaletteItem): void; onClose(): void }) {
  const [query, setQuery] = useState("");
  const hits = useMemo(() => searchAdd(query, personas), [query, personas]);
  return (
    <div
      className="cv-popover cv-quick"
      role="dialog"
      aria-label="Quick add"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <input
        type="search"
        aria-label="Quick add search"
        placeholder="add: file server, AD, router…"
        value={query}
        autoFocus
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && hits[0]) onPick(itemOfHit(hits[0]));
        }}
      />
      {query.trim() ? (
        <AddHits hits={hits} disabled={false} onPick={onPick} />
      ) : (
        <ul className="cv-add-list">
          {DEVICE_CARDS.map((c) => <AddEntry key={c.id} item={cardItem(c.id)} detail={c.line} disabled={false} testId="quick-card" onPick={onPick} />)}
          {TOOLS.map((t) => <AddEntry key={t.id} item={toolItem(t.id)} detail={t.line} disabled={false} testId="quick-tool" onPick={onPick} />)}
        </ul>
      )}
    </div>
  );
}
