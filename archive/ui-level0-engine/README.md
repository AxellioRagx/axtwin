# The level-0 / level-1 layout engine, archived 2026-11-08

Lifted from the frozen `o2/console` (2026-10-30) and retired once the canvas's device tree stopped
reading it. Its geometry had been unused since review round 3, track B; the tree still asked it
WHAT to draw. That is now a direct read of the graph:

| Was | Now |
|---|---|
| `level0.ts` tiles, every site expanded: the shown segments | `graph.segments` (the tree folds per carrier and site itself) |
| `level0.ts` `unplaced.count` | `graph.unplacedLocal.length` |
| `level1.ts` role bands collapsed by occupancy rung | `ui/src/features/edit-canvas/canvas/bands.ts` `segmentBands` |
| `arcs.ts` top-K observed arcs + every conduit, widths | `canvas/pairs.ts` `selectPairs`, merged on the boxes drawn (pill or fold) |
| `scale.ts` log stroke scale | `canvas/pairs.ts` `strokeForBytes` |
| `level0.ts` `overflowAnchorKey` | `canvas/placement.ts` |

The shown set and the bands were checked identical on the 17 catalog twins with a `te.yaml` before
the move. `columns.ts`, `infra.ts`, `sites.ts`, `tiers.ts`, `trunk.ts` were level 0's own.

Still live in `layout/`: `geometry.ts` (sizes), `pins.ts` (the side-car pins), `matrix.ts` (the
list view's census matrix). `../ui-bus-plane/` holds the bus plane's geometry, archived earlier.
Reference only; nothing imports this folder.
