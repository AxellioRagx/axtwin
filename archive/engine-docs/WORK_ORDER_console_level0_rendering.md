# Work Order — Console level 0: make the canvas read as a network (the path, the arcs, the width)

> **STATUS: COMPLETE — every slice in this order is built** (R5 + R7 2026-09-11, R1 2026-09-12,
> R2 + R3 2026-09-14, R4 + R6 2026-09-15). **One follow-up is named rather than left implicit:
> COLUMN WRAPPING** — the map still does not fit at a 1600 px window (plane **2030 × 834**, §8's R6
> block); the residual is structural width, not height; wrapping site columns onto a second row is
> its own slice, unwritten. The original status narrative (incl. the parent/evidence-base notes), §2
> (the as-of-2026-09-11 review this order was drafted from), the per-slice specs (§3 R1–R7), §8's
> R1–R4 "added by" limit blocks and every "built as" record (§9–§14) moved **verbatim** on
> 2026-09-19 to [`archive/work_orders/WORK_ORDER_console_level0_rendering.built.md`](archive/work_orders/WORK_ORDER_console_level0_rendering.built.md) — same headings. The shipped behavior is described present-tense in
> `docs/design/15-console-canvas.md`.
> Authority: **user** for the goal, the sequence and the **arcs ruling** (§4 — east-west links are
> volume-weighted arcs anchored at the site group's bottom edge; this **supersedes** the orthogonal
> one-channel-per-conduit lanes of `docs/design/15-console-canvas.md` §4/§5 of 2026-09-04);
> **implementation (Claude Code)** for module layout, geometry constants, the top-K budget and every
> call marked as one. **D1–D3 take this order's v1 defaults** (§5). **Version-locked: none.**

## 1. Goal

The live level 0 must read as a **network diagram** at a glance — a tiered *path* from the Internet,
through the tap and the WAN edge, down one leg per site group to the segments, with the east-west
traffic in its own band beneath — and it must do so **better** than the report it was modelled on:
the report shows *what* talked to what; the Console shows that **and** the evidence behind every
element, on a layout that stays put across recompiles, with a drill-down an operator can edit.

Three properties, each a test:

1. **There is a path.** Internet → tap → WAN edge → site legs → tiles, drawn as connected geometry,
   with stroke width carrying north-south volume. Removing the tier rails and the trunk must make the
   golden tests fail.
2. **Nothing crosses a tile.** No connector — trunk leg, infra stub, east-west arc — passes through
   any tile's box. This is a computed assertion in the golden tests, not a visual check.
3. **Magnitude is visible.** The busiest east-west pair and the busiest north-south group are
   identifiable by width alone, before any label is read.

## 2. What exists today — archived

The as-of-drafting review (§2, "verified on this branch, 2026-09-11") moved verbatim to
[`archive/work_orders/WORK_ORDER_console_level0_rendering.built.md`](archive/work_orders/WORK_ORDER_console_level0_rendering.built.md).

## 3. Slices

Independently shippable; sizes as in the parent order. Each slice's acceptance is a test unless it
says otherwise. Golden files under `o2/console/test/layout/` are **regenerated once per slice** with
the slice's own commit naming the regeneration — a golden that changes silently is a bug.

| Slice | Status | Spec (archived, §3) | Built-as record (archived) |
|---|---|---|---|
| **R1** — width and rails | BUILT 2026-09-12 | §3 R1 | §10 |
| **R2** — the trunk: Internet → tap → WAN edge → one leg per site (+ the `axr-layout/1` plane stamp) | BUILT 2026-09-14 | §3 R2 | §11 |
| **R3** — infrastructure over its group, role badges on tiles (`PLANE_EPOCH` → 2) | BUILT 2026-09-14 | §3 R3 | §12 |
| **R4** — the east-west band: traffic arcs, top-K, from the group edge (`lanes.ts` deleted) | BUILT 2026-09-15 | §3 R4 | §13 |
| **R5** — close GAP-079: the cast publishes its conduits and router legs (backend) | BUILT 2026-09-11 | §3 R5 | §9 |
| **R6** — tile content and per-group budgets (`PLANE_EPOCH` → 3) | BUILT 2026-09-15 | §3 R6 | §14 |
| **R7** — GAP-082: the summary endpoint's canonical-id check on cast bundles (backend) | BUILT 2026-09-11 | §3 R7 | §9 |

The slice specs (headings `### R1 — …` through `### R7 — …`) are in [`archive/work_orders/WORK_ORDER_console_level0_rendering.built.md`](archive/work_orders/WORK_ORDER_console_level0_rendering.built.md) verbatim.

## 4. Rulings recorded by this order

### 4.1 Arcs, not orthogonal lanes (Authority: user, 2026-09-11)

The 2026-09-04 ruling in `15` §4/§5 — "every conduit runs as an orthogonal lane between the two
tiles it joins", one channel each — is **superseded** for the east-west band. Three routings were
drawn on one fixture (two groups, seven tiles, six real pairs from this imprint):

- **A — orthogonal, one channel per conduit, from the tile** (`lanes.ts` today): every 10.80 row-1
  endpoint drops through the row beneath it (6 of 6 lanes crossed a tile on the fixture); the band
  is one 11 px channel per pair (440 px at 40 pairs); the 2.2 GB and the 5 MB pair are the same
  stroke.
- **B — arcs from the group's bottom edge, width by volume**: nothing can cross a tile; magnitude is
  visible; top-K bounds the band; still a pure function of the same boxes.
- **C — orthogonal from the group edge, one channel per site pair**: no crossings and a short band,
  but a lane no longer names one conduit.

**B is ruled.** The one property A had that B lacks — one lane, one conduit — is preserved for the
*asserted* set by drawing asserted conduits as distinct (edit-colour, dashed when silent) arcs, and
for the complete set by the matrix. `15` §4/§5 are amended in the same commit to say so.

### 4.2 The reference is the floor, not the target

The report at `:8088` is matched by R1–R4 and exceeded by what the Console already carries:
evidence class and confidence as a visual channel on every element, a layout that is stable across
recompiles (S4), and a drill-down S5 made editable and S6 made applicable. None of that is visible
on a grid with no path, which is why this order exists.

## 5. Decisions for the user

> **All three TAKEN 2026-09-11 (Authority: user): D1, D2 and D3 stand at this order's v1 defaults.**
> D3 is therefore the rule R7 implemented ("tolerate on read"), with the per-field classification in
> §9.2; D1 and D2 bind R2 and R4 when those slices are built. Nothing here is outstanding.

- **D1 — Site ordering inside the trunk.** Legs are drawn in site order (prefix order, `misc` last).
  Alternative: order sites by north-south bytes desc so the busiest group sits nearest the WAN box.
  **v1 default: prefix order** (stability over salience; the width already carries salience).
- **D2 — K for the top-K arcs.** v1 default **12**; `?ew=all` shows every pair.
  **Note added by R2 (2026-09-14), because this is where the budget is chosen:** stroke width is
  normalised over **the drawn set's own** log range (§11.3), so **the same element is drawn at a
  different width in the top-12 view and under `?ew=all`** — measured on the pit bundle, a pair at
  **3.06 px** among the top 12 is **4.74 px** across all 40. That is the deliberate cost of making
  twelve arcs discriminable (0.66 px → 2.06 px of spread); it is recorded here so nobody reads a
  width change on toggling `?ew=all` as a change in the data. If K is ever raised or lowered, expect
  every width to move with it.
  **Note added by R4 (2026-09-15), on the budget as BUILT:** K = 12 is implemented and tested, and
  on today's content it is **inert** — because the budget bounds the *observed* set alone while every
  *asserted* conduit is drawn (§4.1's own rule), and both shipped casts declare a conduit for
  essentially every pair they observed: the pit bundle publishes **40 conduits for its 40 observed
  pairs** (so 40 asserted arcs, `hiddenPairs === 0`), `corpapcn-twin` **22 conduits against 31
  observed pairs** (22 asserted + 9 observed, still under K). **Ruled ACCEPT (Authority:
  implementation (Claude Code), 2026-09-15):** the *K = 12 default* above is the user's D2 of
  2026-09-11; the ruling on its **inertness** is this build's, taken on the reviewer's
  recommendation. Budgeting by volume would hide declared-but-silent conduits *first* — they carry
  zero bytes, so they sort last — which is exactly backwards for the element §4.1 exists to
  distinguish; and the band is height-bounded by construction (proven at 300 pairs), so inertness
  costs density, not layout. Two consequences to state plainly: nothing is hidden, so the *show all*
  control is **not rendered at all** on either bundle, and the "3.06 px among the top 12 versus
  4.74 px across all 40" measurement above therefore describes **a view no operator can currently
  open** — it is reachable only by constructing the graph without the roster the API publishes, which
  is how `layout.real.test.ts` tests it (and says so).
  **Note added by R6 (2026-09-15), because the per-group tile budget re-weights the same band:** a
  trunk **leg's** width is **invariant** under that budget and an **arc's** is not, and the contrast is
  deliberate rather than an inconsistency. A leg answers *"how much did this GROUP send across the
  twin's edge"* — a property of the group, not of the drawing — so `layout/sites.ts` sums every
  member, collapsed ones included, and expanding a group leaves every leg byte-identical. An arc
  answers *"how much does THIS CURVE carry"*, and a curve into a collapsed group carries the **merged**
  volume of every pair folded onto that anchor (§14.3), so expanding changes both the bytes on a curve
  and — through the drawn-set normalisation above — the width of every other arc beside it. Measured
  on the pit bundle: expanding every group takes `foldedPairs` **8 → 0** and `hiddenPairs` **20 → 28**
  while the legs hold still. An operator toggling `?expand=` sees exactly that: **the band re-weights,
  the trunk does not.**
- **D3 — GAP-082's rule.** Either the canonical-id check tolerates a baked export `imprint_id` that
  differs from the directory name on a *cast* bundle (the export addresses the observed PIT, which is
  legitimately a different id — the same argument `CastInput.imprintId`'s docstring already makes
  for the SPA), or the cast installer rewrites the export's `imprint_id` to the canonical id at
  `install-cast` time. **v1 default: tolerate on read, warn in `skipped`-style detail**, since the
  installer is aximprint's and the read is ours.

## 6. Tests, goldens, build

`npm run typecheck` clean from zero (the parent's rule); every new pure module gets a golden under
`test/layout/` and a real-bundle assertion in `test/layout.real.test.ts`; `noConnectorCrossesATile`
lives in `test/layout/helpers.ts` and is called by every level-0 golden from R2 on. R5 adds to
`tests/test_api.py`. No new dependency (`15` §9: plain SVG; Bézier paths need none).

## 7. Sequence and gating

R1 → R2 → R3 → R4 → R6 on the SPA, in that order (each builds on the previous layout shape); R5 and
R7 on the API side, in parallel, any time. The parent's S5.1.b (clone‑and‑drag) follows R4; S5.1.a
does not wait. S7 (the Grafana embed) is independent. Nothing here is gated on aximprint.

## 8. Honest limits

- North-south volume is **per window set** (`windows` of the export), not per unit time; labels say
  "in this window", never a rate.
- The inferred WAN edge is a statement that non-local traffic exists, not the discovery of a NAT
  address; the report's `50.209.234.65` egress fact has no counterpart in the export and is not
  drawn.
- Top-K hides pairs from the *map only*; the matrix and the inspector are the complete record.
- A site whose segments are all collapsed by R6's budget still shows its trunk leg and its arcs.

**Added by R1–R4 (2026-09-12 → 2026-09-15)** — archived verbatim to [`archive/work_orders/WORK_ORDER_console_level0_rendering.built.md`](archive/work_orders/WORK_ORDER_console_level0_rendering.built.md)
(headings "**Added by R1, 2026-09-12 (§10):**" … "**Added by R4, 2026-09-15:**"). The R6 block
below is the current state and names the one open follow-up.

**Added by R6, 2026-09-15 (§14):**

- **The map does not fit at a 1600 px window, and after R6 that is INHERENT — accepted, with the
  follow-up named.** Measured on the pit bundle: the plane is **2030 × 834** against a ~1091 px centre
  pane at a 1600 px window, so **939 px are off-screen**; it fits at **2600** with 61 px to spare. R6
  did its job on *height* — the 19-segment column went from **seven tile rows to two plus the overflow
  tile**, and `height` fell **1282 → 834** — but the residual is **width**, and it is structural: three
  groups at six tiles across is `3 × 558 + 152 + 214 + 24 = 2030`, and even `SITE_BUDGET = 4` gives
  **1548**. Lowering the budget further would hide more of the twin to buy pixels the layout cannot
  give back. **The follow-up is COLUMN WRAPPING — site columns onto a second row — as its own slice**,
  and R1's off-screen readout (`· N px off-screen — scroll, or fit width`) covers it honestly
  meanwhile. This supersedes R1's §8 expectation that "R6's per-group budgets are what narrow the
  plane": they narrowed it (2374 → 1514 on the golden fixture, §14.5), and it is still wider than the
  pane on real content.
- **A pin on a collapsed tile is KEPT, not drawn, and not dropped.** It is re-applied at its saved
  coordinates the moment `?expand=` opens the group, and a **site** pin carries the group's `+N more`
  box with the rest of its column. Silently dropping it would destroy work an operator can no longer
  see to recover; drawing it would put a box where the budget says nothing is. The state is disclosed
  rather than left to be inferred — **`· N held for collapsed tiles`** beside the pin count (§14.2).
- **A pinned column still drags its connectors across another column's tiles** — the same no-reflow
  consequence R2 records for the bus, R3 for the stubs and R4 for the arcs, now measured on the
  **narrower** plane. Swept, not sampled (LES-305), and **quoted with its grid**: `x ∈ [140,1400]`
  step 20 × `y ∈ {620,300}` = **132 positions per column** — `site:10.80` **89 clean / 23 trunk / 36
  stub**, `site:10.20` **89 / 31 / 31**. The per-family counts are **not a partition**: one pin can
  violate in two families at once, which is why they are published separately rather than summed. A
  clean coordinate (`site:10.80@(420,620)`) and two violating ones (`site:10.20@(420,620)` and
  `@(420,484)`) are pinned as tests.
- **Every pre-R6 pinned coordinate in the test suite had to be RE-AIMED, because the plane narrowed.**
  That is the cost the epoch bump announces, now paid inside our own tests — and the fix round drew a
  distinction worth keeping: the **violating** coordinates are now *derived from the layout* so they
  keep meaning "a pin that lands over another column" through the next plane change, while the
  **clean** ones stay **literal on purpose**, so a future change falsifies them loudly instead of
  silently re-aiming them (**LES-317**).

## 9–14. "Built as" records — archived

Each slice's record moved verbatim to [`archive/work_orders/WORK_ORDER_console_level0_rendering.built.md`](archive/work_orders/WORK_ORDER_console_level0_rendering.built.md), same headings:

- **§9. R5 + R7 built as, 2026-09-11** (§9.1–§9.7)
- **§10. R1 built as, 2026-09-12** (§10.1–§10.7)
- **§11. R2 built as, 2026-09-14** (§11.1–§11.7)
- **§12. R3 built as, 2026-09-14** (§12.1–§12.6)
- **§13. R4 built as, 2026-09-15** (§13.1–§13.7)
- **§14. R6 built as, 2026-09-15** (§14.1–§14.7)

