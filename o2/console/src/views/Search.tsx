/**
 * Search (`docs/design/15-console-canvas.md` §7): the primary navigation on a
 * large twin. A name, an address, a role or a CIDR jumps to the element at its
 * level via `resolveSearch` (`model/search.ts`, pure). A query that matches
 * nothing says so beside the box rather than navigating nowhere.
 */
import { useState, type FormEvent } from "react";

import type { ConsoleGraph } from "../model/graph";
import { resolveSearch } from "../model/search";
import { zoomToPath } from "../model/zoom";

export interface SearchProps {
  graph: ConsoleGraph;
  go: (path: string) => void;
}

export function Search(props: SearchProps): JSX.Element {
  const { graph, go } = props;
  const [query, setQuery] = useState("");
  const [note, setNote] = useState<string | null>(null);

  function submit(e: FormEvent): void {
    e.preventDefault();
    const result = resolveSearch(graph, query);
    if (result.zoom) {
      setNote(null);
      go(zoomToPath(result.zoom));
    } else {
      setNote(result.note);
    }
  }

  return (
    <form className="search" onSubmit={submit} role="search">
      <input
        type="search"
        placeholder="name / address / role / CIDR"
        aria-label="Search the twin"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          if (note) setNote(null);
        }}
        data-testid="search-input"
      />
      {note && (
        <span className="search-note" role="status" data-testid="search-note">
          {note}
        </span>
      )}
    </form>
  );
}
