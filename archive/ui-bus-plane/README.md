# The bus plane's geometry, archived 2026-11-07

Lifted from the frozen `o2/console` layout engine (tracks 1–4 of the canvas) and retired by
review round 3, track B (`docs/design/canvas-review-2026-11-05.md` R3-4): the canvas draws a
dagre device tree whose edges are routed on its own ranks (`ui/src/features/edit-canvas/canvas/orthogonal.ts`).

| File | Was |
|---|---|
| `wiring.ts` | the legs' polylines over the live node boxes: drops into a bus's port region |
| `route.ts` | an A* router over a sparse gutter grid (`routeAll`), run per render on the bus plane |
| `plane.ts` | the frozen console's level-0 plane stamp (the canvas has its own, `canvas/plane.ts`) |
| `viewport.ts` | the level-0 viewport fit for the frozen console's plane |

The level-0 / level-1 engine followed on 2026-11-08: `../ui-level0-engine/`.
Reference only; nothing imports this folder.
