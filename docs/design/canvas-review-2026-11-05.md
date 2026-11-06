# Canvas review, 2026-11-05 — the round-2 bugs and their fixes

**Status:** the review was written by a Claude-desktop browser session testing
`/canvas/canvas-test` on the running build (Claude Doc `PR6PeoXpMPjcfMikVX69qP`:
an edit → Save-As → Apply round trip, ten bugs by severity, six UX confusions).
Round 1 was worked on 2026-11-06 (`65dd86b`..`1ed813c`); the reviewer re-tested
the pushed build the same day ("Round 2" in the doc), found everything else
fixed and left five items. This file is the repo's record of that round; the
fixes are `ac93980` (R2-1..R2-4) and the rendering rules of
`ui/src/features/edit-canvas/SPEC.md` (R2-2 again, by design).

## Round 2

| # | Issue (the reviewer's words) | Expected | Fix |
|---|---|---|---|
| R2-1 | Undo stack not persisted across reload (listed as fixed, is not): edit, Save layout, reload | Undo enabled after reload; Redo disabled when its stack is empty | The stack is stored per document (`axr-canvas-undo:<deploy id>`, or `twin:<id>` for an undeployed twin, which the first fix had left out) once the text is at rest on the server, and restored only when it still FITS the text loaded (`CanvasPage.tsx` `stackFits`: the last command's after-text, or with everything undone the next redo's before-text, path for path). The reviewer's exact sequence did not reproduce on the old build (two live runs kept Undo), so the content check replaces the `updated_at` key as the safer rule. Redo is `undone.length > 0`, nothing else. |
| R2-2 | A dragged segment leaves its site group; the site box keeps its old size with a hole: drag `office` upward out of `10.70` | site resizes around its children, or the drag is constrained to the parent | The site resizes: its box is the union of its packed box and every child's box with its padding, in all four directions (`placement.ts`). A child is deliberately not clamped (`extent: "parent"` would move a saved pin). Rendering rule 3 then demoted the group to a borderless tint, which is what the brief asked for. |
| R2-3 | Edge to a moved segment draws as a flat horizontal line to the box's top edge, through the router icon | handle positions recomputed after a group move | A leg no longer runs handle to handle: it is a straight line from the border of the node it leaves towards the centre of its segment, clipped at both borders (`canvas/legs.ts` `clipLine`; a router's box is its 34 px icon, not its span over the segments), so it meets the box on whichever side faces the router; overlapping boxes get the centre-to-centre line. React Flow re-renders the edge with the node internals, so it follows a drag live. |
| R2-4 | Drag hit-testing: a drag starting on a segment header under an edge selects but does not move; clicking the lower part of a host chip selects the parent segment | edges below nodes for pointer events; chip hit box equals its drawn box | The legs keep their z above the boxes but take NO pointer events (`LEG_CLASS`, `canvas.css`); the pane finds the leg under the pointer itself (`Canvas.tsx` `legAt`: nearest within 8 screen px, not snapped to the grid, only over the pane or a site's background -- a chip, segment or router wins) for the hover label, the click and the menu. The chip half-click did not reproduce by element hit-test at zoom 1 or 2; its likeliest cause was the leg's 14 px interaction stroke over the chip, which is gone. A label-less chip (rule 4) is sized to its ring, so the hit box stays the drawn box. |
| R2-5 | Not retested: tfc-replay Cancel; the `add a leg on…` native select (programmatic set works) | verify by hand | Done by hand on 2026-11-06 (Playwright, `scratchpad/r2c.cjs`): a persona change on `file-server`, Apply, Cancel 2 s into the tfc replay → `409 cancelled` in 4 s, the pill `tfc ✗ failed`, the tier `attempted`, Reset replayed the baseline (`reverted tfc`). The attach select on a host chip takes a keyboard pick (ArrowDown registers the value and enables Attach); the reviewer's failure was its automation's `selectOption` not firing React's change. |

## Round 1 (for the record)

Wedged deployed twin (Reset always failed on a baseline persona that could not
play; Undo empty after reload; Apply blocked) · Reset hung ~90 s with a stale
banner · auto-layout re-ran on every mutation and stacked segments · edges
behind segments · the bar said "deploy completed" while tfc had failed · rename
broke the selection · the palette was the whole catalog · Save-As dropped the
layout draft · minor: hover card, "Evidence: no evidence" on authored twins,
toast over the Internet node, the bar wrapping, the twin page's discovery
fields. All worked 2026-11-06; `docs/SESSION_STATE.md` of that date lists the
commits. Still open from it: the canvas cannot author `install:` (the real
reason the reviewer's file server ran no smbd).
