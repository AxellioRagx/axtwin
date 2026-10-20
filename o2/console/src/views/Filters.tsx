/**
 * The filter bar (`docs/design/15-console-canvas.md` §7): `?ghosts=` /
 * `?class=` / `?occupancy_lt=`. The filters ARE the URL, so every control is a
 * `<Link>` to the same path with a different query -- the back button and a
 * bookmark then work for free, and `model/zoom.ts` stays the one grammar.
 */
import { Link } from "react-router";

import { EVIDENCE_CLASSES, type EvidenceClass } from "../model/evidence";
import { clearFilters, filtersToQuery, type Filters } from "../model/zoom";

export interface FiltersBarProps {
  filters: Filters;
  /** The current pathname (without query) -- filters do not change the level. */
  pathname: string;
}

const OCCUPANCY_PRESETS = [0.5, 0.8];

export function FiltersBar(props: FiltersBarProps): JSX.Element {
  const { filters, pathname } = props;
  const to = (next: Partial<Filters>): string =>
    `${pathname}${filtersToQuery({ ...filters, ...next, problems: [] })}`;

  // S2: the clear control's TARGET and its VISIBILITY both derive from the one
  // taxonomy helper (`model/zoom.ts`), so a future flag cannot end up cleared
  // but not counted -- or counted but not cleared -- by being forgotten at one
  // of the two sites.
  const clearedQuery = filtersToQuery(clearFilters(filters));
  const canClear = clearedQuery !== filtersToQuery({ ...filters, problems: [] });

  return (
    <div className="filters" data-testid="filters">
      <span className="filters-label">filters</span>

      <Link className={filters.ghosts ? "chip on" : "chip"} to={to({ ghosts: !filters.ghosts })}>
        {filters.ghosts ? "✓ " : ""}ghosts
      </Link>

      <span className="filters-group">
        class:
        <Link className={filters.evidenceClass === null ? "chip on" : "chip"} to={to({ evidenceClass: null })}>
          any
        </Link>
        {EVIDENCE_CLASSES.map((cls: EvidenceClass) => (
          <Link
            key={cls}
            className={filters.evidenceClass === cls ? "chip on" : "chip"}
            to={to({ evidenceClass: filters.evidenceClass === cls ? null : cls })}
          >
            {cls}
          </Link>
        ))}
      </span>

      <span className="filters-group">
        occupancy&lt;
        {OCCUPANCY_PRESETS.map((v) => (
          <Link
            key={v}
            className={filters.occupancyLt === v ? "chip on" : "chip"}
            to={to({ occupancyLt: filters.occupancyLt === v ? null : v })}
          >
            {v}
          </Link>
        ))}
      </span>

      {/* The post-apply "changes" filter (`15` §12 O5) is turned ON by an apply,
          not by the operator, so it only appears here to be turned OFF once the
          changed set no longer needs emphasizing. */}
      {filters.changed && (
        <Link className="chip on" to={to({ changed: false })} data-testid="filter-changed">
          ✓ changes
        </Link>
      )}

      {canClear && (
        // Clears the FILTERS, not the view: `?summary=` travels in the same
        // query, so linking to the bare pathname would collapse the summary
        // bar as a side effect of clearing a class filter.
        <Link className="chip clear" to={`${pathname}${clearedQuery}`} data-testid="filters-clear">
          clear
        </Link>
      )}
    </div>
  );
}
