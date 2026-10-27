# WORK_ORDER_console_level0_rendering — built record (archive)

Moved here **verbatim** from
[`docs/WORK_ORDER_console_level0_rendering.md`](../../WORK_ORDER_console_level0_rendering.md) on
2026-09-19, when every slice (R1–R7) was built, under the work-order size rule in
`docs/CONVENTIONS.md` §3–§4: a work order keeps the *active* spec; a built slice's record lives here.
Blocks appear in their original order, each preceded by an HTML comment giving its original line
range, and every heading is unchanged — so a citation such as "§11.4", "§3 R4" or "§8's R6 block"
resolves to the same text here. Nothing was summarized or dropped.

<!-- original lines 3-39 of WORK_ORDER_console_level0_rendering.md -->
> **STATUS: EVERY SLICE IN THIS ORDER IS BUILT.** R5 + R7 BUILT 2026-09-11 (§9); R1 BUILT 2026-09-12
> (§10); R2 BUILT 2026-09-14 (§11); R3 BUILT 2026-09-14 (§12); R4 BUILT 2026-09-15 (§13);
> **R6 BUILT 2026-09-15 (§14)** — with it, **every RENDERING slice (R1–R4, R6) and both backend
> slices (R5, R7) are done**, and the ten-iteration run moves from drawing to editing.
> **One follow-up is named rather than left implicit: COLUMN WRAPPING** — the map still does not fit
> at a 1600 px window (plane **2030 × 834**, §8's R6 block), and the residual is structural width, not
> height; wrapping site columns onto a second row is its own slice, unwritten. Drafted 2026-09-11.
> **The user ruled the whole Console be built in order, 2026-09-11 (Authority: user):** R1–R7 here,
> then `docs/WORK_ORDER_console_canvas.md` §3 S5.1 (a–e), then S7 — as a ten-iteration run whose
> **iteration 1 was R5 + R7** (the two backend slices, which §7 says run in parallel with anything),
> whose **iteration 2 was R1** (width and rails), whose **iteration 3 was R2** (the trunk), which
> also discharged R1's one deferred decision — the layout **plane/epoch stamp** on `axr-layout/1`
> (§8, §11.4) — and whose **iteration 4 was R3** (infrastructure over its group, role badges on
> tiles), which took that stamp's **epoch to 2** (§12.3), whose **iteration 5 was R4** (the
> east-west arcs, §13 — which moved no tile and left the epoch at 2), and whose **iteration 6 was R6**
> (tile content and per-group budgets, §14 — which changed *which tiles exist* and took the epoch to
> **3**). **Iteration 7 is `docs/WORK_ORDER_console_canvas.md` §3 S5.1.a.**
> **D1–D3 take this order's v1 defaults** (§5), so nothing below is waiting on a decision.
> Authority: user for the goal ("the canvas UI should look
> better than the corp-apcn topology report at `10.80.72.52:8088`"), for the sequence (all six slices,
> R1–R6), and for the **arcs ruling** (§4 — east-west links are drawn as volume-weighted arcs
> anchored at the site group's bottom edge; this **supersedes** the orthogonal one-channel-per-conduit
> lanes ruled in `docs/design/15-console-canvas.md` §4/§5 on 2026-09-04). Authority:
> **implementation (Claude Code)** for module layout, geometry constants, the top-K budget and every
> call marked as one — each open to veto. **Version-locked: none intended** — no `IR_SCHEMA` /
> `PLANNER` / `OP_CATALOG` / `VALIDATOR` / `ADAPTER` / `IMAGE_MANIFEST` change, no new op type, no
> schema bump; R5 is an additive DTO field on an existing route.
>
> **Parent:** `docs/WORK_ORDER_console_canvas.md` (S1–S6 BUILT, S7 OPEN). This order changes what
> level 0 *draws*, not what it edits: the built S5 gestures and S6 apply keep working on the level 0
> this order produces (editing is not layout — §16's goldens rule). The S5.1 editing amendment in the
> parent (`§3 S5.1`) drops its pending clone tile into R1–R4's geometry, so R1–R4 precede S5.1.b;
> R5/R7 are backend and run in parallel with anything.
>
> **Evidence base:** a review of the live app at `http://10.80.72.52:8080/imprints/pit-395a61f04e-1780405200`
> against the reference report and the `o2/console` source on this branch, 2026-09-11 (§2). The
> ruling was taken on a side-by-side of the three routing options drawn on one fixture (§4.1).

<!-- original lines 59-89 of WORK_ORDER_console_level0_rendering.md -->
## 2. What exists today (verified on this branch, 2026-09-11)

**The design ruled a tiered path diagram (`15` §4); the mockup drew one; the live build ships a grid
of identical tiles with no path.** Findings, each tied to the code:

| # | Live symptom | Cause in the code | Reference does instead |
|---|---|---|---|
| F1 | No path. The Internet pill floats above the tiles, unconnected; no WAN-edge box. | `layout/level0.ts` draws `wanEdge` only when `graph.routerSegmentNames.length > 0`; the live `CastInput` (`views/useGraph.ts`) never sets `routerSegments`. No trunk geometry exists in `views/Level0.tsx` — the only connectors are the device tie-lines. | Internet → `TAP` badge → WAN edge (NAT) → one thick leg per group, width by bytes. **✅ fixed in R2 (§11.1)** — the WAN box is now keyed on `graph.wanEdge` (declared **or** inferred), and the trunk is pure geometry in `layout/trunk.ts`. |
| F2 | East-west band empty, with a note. | `LIVE_CONDUITS = []` (`useGraph.ts`); `CastSummary` carries a conduit count, not the roster (GAP-079). | Arcs below the segments, one per pair, width by volume. **Cause fixed in R5** (the roster is published and the band draws); **✅ geometry fixed in R4 (§13.1)** — `layout/arcs.ts` draws volume-weighted arcs over R2's `layout/scale.ts` (§11.3), `lanes.ts` is deleted, and on the pit bundle the band draws 40 arcs. |
| F3 | Diagonal lines cross tiles from the infra row. | `Level0.tsx` draws `<line class="tie">` from each device's bottom-centre to its tile's top-centre, across whatever lies between. | Infra chips connect to the *group* with a short orthogonal stub; roles also appear as badges on tiles. **✅ fixed in R3 (§12.1)** — a device's x is now the centre of the column holding its segment, the connector is a published `infraStubs` vertical, and the stubs join `noConnectorCrossesATile`. On the pit bundle the seven devices distribute 2/3/2 and no connector touches a tile. |
| F4 | Lanes would cross tiles once conduits arrive (visible in the mockup's `Main.dc.html`). | `layout/lanes.ts` routes each lane from the **tile's** bottom-centre straight down to its own 11 px channel: a row-1 tile drops through every row beneath it; 40 pairs ⇒ a 440 px band. | Arcs originate at the group box's bottom edge, never inside it. **R2 made it MEASURABLE, not fixed:** `noConnectorCrossesATile` (§6, §11.5) now runs on every level-0 golden and covers the lanes as well as the trunk. It passed on both fixtures — which bounded the claim to *those* fixtures rather than retiring F4. **✅ fixed in R4 (§13.1):** `lanes.ts` is deleted, every arc is anchored **outside** every box, and the drop-before-curve departure (§8, §13.2) is what makes that hold on real content rather than on the fixtures alone. |
| F5 | Every tile the same size; nothing is important. | `TILE_W/TILE_H` fixed; `SITE_COLS = 3` fixed (the 16-segment 10.80 site becomes a 6-row column); tile shows occupancy %, an unlabeled 8 px role bar and an 8 px evidence ring — no bytes, no named roles. | Tiles ranked, `+N more · expand`, each tile shows hosts · bytes · role badges. **Partly fixed in R3 (§12.2):** the unlabeled bar is gone and the tile now **names** its roles (`DC 2`, `EP 14`), with the projection disclosed on hover *and* in the accessible name. **✅ closed in R6 (§14.1–§14.2):** tiles are **ranked** by placed-host count (`?order=cidr` the other order), a group draws at most **six** with a `+N more` tile that discloses the **volume** it hides, the tile carries **`CIDR · hosts · N-S and E-W bytes · role badges · evidence ring`**, and `SITE_COLS` follows the shown count. **One clause of the symptom is deliberately NOT implemented — tile size does not vary** (§14.1, an implementation call open to veto): magnitude is carried by the byte figure and by connector width, because a tile whose size followed its volume would move every other tile in its column whenever a host appeared, which is the column-preserving locality `layout0` exists to hold. |
| F6 | Illegible. | `svg.level0 { max-width:100%; height:auto }` (`theme/shell.css`) fits a ~1,000-unit layout into a ~700 px centre pane under `CastNumbers` + `EvidenceMix` + Search + Filters (`MapPane.tsx`); ~60 % scale ⇒ 11 px type renders at ~6 px. | The diagram owns ~930 px at a 1,246 px window; text 11–13 px actual. **✅ fixed in R1 (§10.1/§10.3).** |
| F7 | No tier rails. | `15` §4 names them; the mockup has them; `Level0.tsx` does not draw them. | `INTERNET / WAN EDGE / INFRASTRUCTURE / SEGMENTS / EAST-WEST` rail labels. **✅ fixed in R1 (§10.2).** |
| F8 | Tap/vantage absent from the map. | `TopologyExport.vantage` (`axellio-hq/corp-apcn-rdr1:12`) and `windows` are in the DTO (`api/types.ts`) and unused by level 0. | A dashed `TAP` badge on the trunk. **✅ fixed in R2 (§11.2)** — labelled **verbatim** from `vantage`/`windows`, because the two real bundles spell them differently. |
| F9 | `GET /api/imprints/pit-395a61f04e-1780405200` → **400** (not 500 — corrected when R7 was built) *directory name (canonical) disagrees with provenance.imprint_id=None*. And it hit **every** cast bundle, not just this one: the check being applied is the *normalized* sidecar's id rule, and a **canonical** sidecar declares no imprint id at all. | `axrange/api/imprints.py` `_cross_check_ids`. The canvas survives only because it reads `/topology` + `/cast` directly. | — (GAP-082; **fixed in R7**, §9) |

**The data for all of it is already in the browser.** `GET …/topology` on this imprint delivers
2,641 nodes / 4,157 edges, each edge with `src`, `dst`, `proto`, `bytes`, `flows`, `evidence_class`,
each node with `segment`, `locality`, `role`. Aggregated in the page: 2,393 external vs 248 local
entities; north-south bytes per segment (10.20.195.0/24 the largest egress talker, 1.77 GB in the
window); **40 segment-pair east-west links** (10.80.20.0/24 ↔ 10.80.75.0/24 dominates at 2.2 GB — the
same arc the report highlights); protocol mix per pair; 2 domain controllers, 7 internal resolvers,
3 file servers, 1 mail, 1 monitoring by role. F1–F5 and F8 are therefore **renderer** problems, not
data problems. GAP-079 (the asserted roster) is still worth closing (R5) but does not block any of it.

**What stays.** `layout0` as a pure, golden-tested function of identity; S4 pins (a pinned tile or
site still moves as an absolute override, and every connector re-anchors to the moved box); the
evidence encoding of `15` §6; the six-class palette in `theme/tokens.css`; the three-pane shell and
the URL grammar.


<!-- original lines 96-281 of WORK_ORDER_console_level0_rendering.md -->
### R1 — Width and rails — **S, ungated** — ✅ **BUILT 2026-09-12 (§10)**

*The half-day that fixes "I can't read it".*

- `MapPane.tsx`: the summary strip (`CastNumbers`, `EvidenceMix`) becomes a **collapsible one-line
  bar** above the drawing (collapsed by default at level 0, state in the URL as `?summary=1` so it
  survives a reload — not in a store); Search and Filters stay, on one row.
- `App.tsx` / `shell.css`: the outline pane is **collapsible** (a rail toggle; state in
  `localStorage` is acceptable here — it is a per-viewer convenience, not layout).
- `shell.css`: `svg.level0` drops `max-width:100%; height:auto`. The map scrolls in both axes at a
  **minimum scale of 1 layout unit = 1 CSS px** and a **fit-to-width** control that never scales
  below **0.8**; below that the map pans. Chrome fonts are set in px on the SVG root, so scale changes
  are visible and bounded.
- `layout/level0.ts` + `Level0.tsx`: **tier rails** — a left-edge label per tier from `tiers.ts`'s
  `TIERS` (`INTERNET / WAN EDGE / INFRASTRUCTURE / SEGMENTS / EAST-WEST`), the segment tier's rail
  spanning the full tile extent. `Level0Layout` gains `rails: Array<{ tier: Tier; box: Box }>`.

**Acceptance.** At a 1,246 px window with the outline open, the level-0 SVG renders at ≥ 0.8 scale
(a jsdom test asserts the computed transform); rail boxes appear in `level0.golden.json` and are
stacked in `TIERS` order with no overlap; the summary bar's state round-trips through the URL.

### R2 — The trunk: Internet → tap → WAN edge → one leg per site — **M, ungated** — ✅ **BUILT 2026-09-14 (§11)**

*The slice that turns the grid into a path.*

- `model/graph.ts`: `buildGraph` gains **north-south aggregation** from `edges` — for every local
  segment, the sum of `bytes` and `flows` over edges whose other end is non-local — as
  `GraphSegment.northSouth: { bytes: number; flows: number }` and per site as
  `Site.northSouth` (summed in `layout/sites.ts`). The WAN edge is **inferred**: `graph.wanEdge =
  { inferred: true, evidenceClass: "observed" }` whenever any local↔non-local edge exists; when the
  cast later publishes router legs (R5) it becomes `{ inferred: false, … }` and the label changes.
  Nothing is invented: an inferred edge is labelled *egress (inferred from observed traffic)*.
- `layout/trunk.ts` (**new**, pure): given the Internet pill, the tap, the WAN box and the site
  headers, returns orthogonal polylines: Internet → tap → WAN (one vertical), WAN → a horizontal
  bus → one vertical **leg per site group** ending at the group header's top-centre. Legs never enter
  a group box. Stroke width is `1 + 5 · normalised log10(bytes)` (the same scale R4 uses, shared in
  `layout/scale.ts`); a group with zero north-south bytes gets a hairline leg, never none.
- `layout/level0.ts`: the **tap badge** sits on the Internet→WAN trunk, labelled from
  `TopologyExport.vantage` and `windows` (`Level0Layout.tap: { box: Box; vantage: string | null;
  windows: number | null }`); the WAN box is drawn whenever `graph.wanEdge` exists.
- `Level0.tsx`: draw the trunk, the tap, the legs; byte labels on legs above a threshold.

**Acceptance.** `level0.golden.json` carries the trunk, the tap and one leg per site; a
`noConnectorCrossesATile(layout)` helper (added to `test/layout.golden.test.ts` and reused by R3/R4)
passes; the real-bundle test asserts the leg to the site holding 10.20.195.0/24 is the widest;
deleting `layout/trunk.ts`'s output from the layout fails the golden. A bundle with no non-local
edges renders no WAN box and no trunk, with an in-UI note — never an invented edge.

### R3 — Infrastructure over its group, role badges on tiles — **S, ungated** — ✅ **BUILT 2026-09-14 (§12)**

- `layout/level0.ts`: an infra device's x is the **centre of the site column that holds its
  segment**, not the canvas centre; devices sharing a column are laid out left-to-right in role
  order (`roles.ts`) and wrap to a second row if they exceed the column width. A device whose segment
  has no tile (unplaced) stays centred with a dashed outline, as today. *(As built: the placement
  lives in its own pure module `layout/infra.ts`, modelled on R2's `trunk.ts` so `applyPins` can
  re-run it over moved columns — §12.1. An unplaced device also gets **no stub** and its **own row**
  below every column's, so it can no longer overlap a placed one.)*
- The **tie-line is replaced by a stub**: a short vertical from the device's bottom to the group
  header's top (`Level0Layout.infraStubs`). Orthogonal, never entering the box. *(As built: to the
  header's **nearest** horizontal edge, via a shared `geometry.nearestEdgeY` R2's legs now use too —
  a group a pin has dragged above the band is approached from below.)*
- `GraphSegment.roleBadges: Array<{ role: string; count: number }>` — up to three, **an
  infrastructure role first, then by count** (corrected below), from the existing `roleHistogram`;
  the unlabeled histogram bar is removed from the tile.

> **Wording correction, 2026-09-14 (Authority: implementation (Claude Code); reviewer ruled KEEP).**
> This slice originally read "up to three, **highest count first**", which **contradicts its own
> acceptance clause** on the data that ships. Measured: a pure count ranking gives
> `10.80.20.0/24` → `EP 9 / WS 7 / NONE 5`, hiding **both domain controllers** (and `dns-resolver`
> on the twin bundle), so the acceptance below ("a `DC` badge with count 2") could never pass. The
> two buckets that win on count — `generic-endpoint` and `unroled-endpoint` — are *absences of a
> fingerprint* rather than roles, and they say the same thing on nearly every segment of a corporate
> twin. **As built: slots are reserved for `INFRA_DEVICE_ROLES`** (deliberately the same set that
> earns a box in the infrastructure band, not a second vocabulary), then filled by count, with the
> fixed role order as the tie-break. **Cost, measured rather than asserted:** the reservation changes
> the badge set on **4 of 39** pit segments and **3 of 32** corpapcn segments, and across both
> displaces a non-`generic-endpoint`/`unroled-endpoint` role **exactly once** (`10.80.20.0/24` on
> corpapcn: `corp-workstation 3` gives way to `DC 1` + `DNS 1`). **Widening to four badges was
> refused** on arithmetic — badge width is `(140 − 2·4)/3 = 44` units at 9 px, and a fourth drops it
> to 32, where labels overflow. Because the set is deliberately **not** a ranking, the tile discloses
> the projection ("3 roles shown of 5 … +2 more in the inspector") on hover **and** in its
> `aria-label`, both from one helper (§12.2, LES-303, LES-307).

**Acceptance.** Golden: every infra device's box x-range lies within its site column's x-range;
`noConnectorCrossesATile` holds for stubs; the real-bundle test asserts the two domain controllers
sit over the 10.80 column and that the 10.80.20.0/24 tile carries a `DC` badge with count 2.
**All three pass (§12.5)** — the badge clause only because of the reservation above.

### R4 — The east-west band: traffic arcs, top-K, from the group edge — **M, ungated — the arcs ruling** — ✅ **BUILT 2026-09-15 (§13)**

*Two departures from the implementation sketch below were forced by measurement and are recorded in
§8 and §13.2: an arc **drops to the band top before it curves** (a cubic reaches only ¾ of the way
toward its controls, so the literal shape crossed tiles), and the control depth is anchored at
**`max(bandTop, both endpoints)`**. The **anchor** the ruling names — the group box's bottom edge —
is unchanged, and is asserted for all 40 arcs on the real bundle.*

- `model/graph.ts`: **`trafficPairs`** — aggregate `edges` by the unordered pair of the two ends'
  segments (both local and placed), carrying `bytes`, `flows`, and `protoMix: Array<{ proto; bytes }>`
  in descending order; sorted by bytes desc then by pair key. Same-segment edges are excluded (they
  are level 1's). Edges with an unplaced or non-local end are excluded here (north-south is R2).
- `layout/arcs.ts` (**new**, pure) **replaces `lanes.ts` for the observed set**: for each pair, a
  cubic Bézier from `(tileA.centreX, siteA.box.bottom)` to `(tileB.centreX, siteB.box.bottom)` with
  a control depth `bandTop + min(D_MAX, D_MIN + k · |Δx|)` — a deterministic function of the two
  boxes; stroke width from `layout/scale.ts`; the band's height is the deepest arc + padding. Only
  the **top-K by bytes** are laid out (**K = 12**, an implementation call; `?ew=all` overrides);
  the remainder is reported as `Level0Layout.hiddenPairs: number` and named in the UI ("N smaller
  links hidden · show all"). The complete set stays in the matrix, unchanged.
- **Asserted conduits keep `lanes.ts`'s pair resolution but not its geometry**: when a roster exists
  (tests today, R5 live), each conduit is drawn as an arc in the **edit colour**, dashed if it has no
  observed traffic, so an asserted-but-silent conduit is visibly different from an observed-but-
  unasserted flow. `lanes.ts`, its goldens and the "22 lanes" real-bundle assertion are **retired
  in this slice** and replaced by "22 asserted arcs"; the retirement is named in the commit.
- Pins: `applyPins` re-anchors arcs from the moved tile/site boxes exactly as it re-anchors lanes
  today (`level0.ts` §"Re-anchor the east-west lanes").
- Colour: arcs are **not** protocol-coloured on the map (`15` §5 stands: colour is evidence on the
  Console); the dominant protocol and the mix are on hover and in the inspector.

**Acceptance.** Real bundle: `trafficPairs.length === 40`, the top pair is
`10.80.20.0/24 ↔ 10.80.75.0/24`, and its arc is the widest; every arc's start and end y equals its
group's bottom edge; `noConnectorCrossesATile` holds; the band height is bounded by
`D_MAX + padding` regardless of pair count; a pinned tile's arcs follow it and an unpinned
neighbour's arcs are byte-identical (extends the S4 golden). A no-edges bundle renders an empty band
with the note "no observed east-west traffic in this window" — distinct from GAP-079's old note,
which is deleted.

### R5 — Close GAP-079: the cast publishes its conduits and router legs — **S, ungated, backend** — ✅ **BUILT 2026-09-11 (§9)**

*The field names below were changed when this was built — see §9.1. As shipped:
**`conduit_roster`** (not `conduits`, which is the existing count) and **`routers: [{name,
segments}]`** (not a flat `router_segments`).*

- `axrange/api/dtos.py`: `CastSummaryDTO` gains **additive** `conduits: list[ConduitDTO]`
  (`{from, to}` segment names, as the bundle's `<id>.yaml` `conduits:` block spells them) and
  `router_segments: list[str]` (the segment names the `kind: router` node has a leg on). Both
  default to `[]`; the count fields stay.
- `axrange/api/imprints.py`: populate both from the twin config already read for the summary.
- `o2/console/src/api/types.ts`: mirror the two fields (LES-258); `views/useGraph.ts`: delete
  `LIVE_CONDUITS` and `conduitsAbsent`, pass `cast.data.conduits` / `router_segments` into
  `buildGraph`. `graph.wanEdge.inferred` becomes `false` when router legs are present; the WAN
  label changes to *gateway · N legs · SNAT*.

**Acceptance.** `tests/test_api.py`: the summary for the standing `corpapcn-twin` bundle carries 22
conduits and the router's leg list; a sidecar-less bundle carries `[]` for both and still lists. The
SPA's live-api test asserts the arcs band shows asserted arcs against the dev server. GAP-079 →
✅ Resolved with this commit.

### R6 — Tile content and per-group budgets — **M, ungated** — ✅ **BUILT 2026-09-15 (§14)**

- Tile: `CIDR · hosts · bytes (north-south + east-west) · up to 3 role badges · evidence ring`.
  Occupancy stays as the fill saturation it already is.
- Ranking inside a group: **by placed-host count desc, then CIDR** (an implementation call; CIDR
  order remains available as `?order=cidr`). The golden documents both orders.
- Per-group budget: **at most 6 tiles shown per site group** (`SITE_BUDGET`, an implementation
  call); the rest collapse to one `+N more · expand` tile at the end of the group; `?expand=<site
  key>` opens it (URL, not store). Collapsed segments are still in the outline, the matrix and search
  — collapsing is a map projection, never a filter.
- `SITE_COLS` becomes a function of the group's *shown* count (2 for ≤ 4, 3 otherwise) so a group is
  never a one-tile-wide tower; still deterministic.

**Acceptance.** Golden: the 10.80 site shows 6 tiles + one `+10 more` tile; expanding it via the
URL yields the full 16 with every previously-shown tile at an unchanged position (locality holds for
the shown set); a collapsed segment remains reachable by search and by the outline; a pinned tile
that is later collapsed keeps its pin and reappears at it when expanded.

> **Wording correction, 2026-09-15 (Authority: implementation (Claude Code); the same correction R3's
> badge rule got above).** The literal figures in that clause — "6 tiles + one `+10 more`", "the full
> 16" — are **bundle-specific**: they describe a **16-segment** `10.80` site, which is
> `corpapcn-twin`/`corpapcn-golden`, *not* the bundle the live Console is pointed at. The pit bundle
> (`pit-395a61f04e-1780405200`) has **19** segments in `10.80` and therefore yields **`+13`** and an
> expanded 19. **As built, the acceptance is asserted as a RULE per bundle, never as a literal:**
> `shown === min(SITE_BUDGET, total)` and `hidden === total − shown`, with the on-screen label derived
> from the same two numbers so the sentence and the drawing cannot drift (LES-312). The literal
> figures are kept above as the record of what the order said; §14.6 states the rule as tested.

### R7 — GAP-082: the summary endpoint's canonical-id check on cast bundles — **S, ungated, backend, unblocking only** — ✅ **BUILT 2026-09-11 (§9)**

Not a rendering slice; recorded here because the review hit it. `_cross_check_ids` in
`axrange/api/imprints.py` refuses `pit-395a61f04e-1780405200` because the export's baked
`imprint_id` (`corpapcn-disco`) disagrees with the directory name, while the canvas's `/topology`
and `/cast` reads succeed. Decide the rule (§5 D3) and make the three routes agree.

**Built under D3's v1 default ("tolerate on read"), and it turned out to be considerably more than
an id check** — see §9.2 for the rule as shipped, §9.3 for the plan-build bug the lifted refusal
exposed (which had made "deploy as twin" dead on every real cast bundle), and §9.5 for the cost the
guard introduced and how it was paid back.


<!-- original lines 384-497 of WORK_ORDER_console_level0_rendering.md -->
**Added by R1, 2026-09-12 (§10):**

- **A pin stored as ABSOLUTE coordinates silently re-points by R1's +128 shift.** `axr-layout/1`
  stores a pin as a position on a plane it does not identify, so moving the plane's origin (the rail
  gutter) moves every stored pin's meaning without changing its bytes. **Blast radius today is
  zero** — the only `layout.json` in the tree holds `"pins": {}` — so R1 **accepts and publishes**
  the limit rather than migrating content that does not exist. **Decision, scoped to iteration 3
  (Authority: implementation (Claude Code)): R2 adds a plane/epoch stamp to `axr-layout/1`**, so a
  client can offset or refuse stale pins. It belongs to R2 and not to R1 because R2, R3, R4 and R6
  each move the plane again and R6 changes *which tiles exist* — one stamp designed against the
  whole series is worth more than four migrations (LES-289).
- **At 1246–1600 px the INTERNET and WAN EDGE rails read as empty.** The centred top chrome sits at
  layout x≈1141 on a 2374-unit plane, so at 1:1 on a narrow window the operator sees the rails and
  the tiles beneath them but must pan right to reach the pill and the WAN box. Deferred
  deliberately: **R2's trunk is what makes that chrome read as a path** and **R6's per-group budgets
  are what narrow the plane**, so re-anchoring the chrome now would reverse the ratified anchoring
  decision (LES-261), churn the goldens twice, and look wrong once the trunk exists. Scrolling to
  content on first paint was considered and **refused** — it would scroll away the very rails this
  slice delivers. The cheap honest mitigation shipped instead: the scale readout states `N px
  off-screen — scroll, or fit width`.
- **The `atFloor`/`belowFloor` knife-edge is not separately revert-provable** with the shipped
  fixture (it needs a plane that fits at *exactly* 0.8). The change is strictly more correct — the
  truncation claim is read off the **unclamped** ratio, so a plane that is precisely whole never
  claims to be cut — and the surrounding readout is pinned by tests.

**Added by R2, 2026-09-14 (§11):**

- **A site pinned ACROSS the bus still makes the trunk cross tiles.** This is inherent to an
  absolute override with **no reflow** (`15` §4): nothing re-routes around a pinned box, so a column
  dragged until its tile rows straddle the bus line is crossed by the bus, and dragged far enough
  above it, by its own leg. R2 fixes the case it can — a group pinned *above* the bus is approached
  from below and its leg stops at the header's **bottom** edge — and **measures** the rest rather
  than claiming it away: clean at pin y=620 and y=300 (below the bus) and at y=138 and y=24 (above
  it), violating at y=100. The coordinates are pinned by a test asserting `> 0` whose failure
  message tells the next reader to **delete both the test and this note** if the limit ever empties.
- **The two-argument save seam is not compiler-enforceable.** `planeForSave` needs the caller to
  pass what it authored alongside the pins, but TypeScript's parameter-arity rule accepts a
  one-argument function wherever a two-argument one is expected, so a caller that silently drops the
  second argument **type-checks**. The widened prop types make the contract *visible*; the
  behavioural guard is a test (a wrapper dropping the second argument fails exactly 1 of 475).
  Converting the callback to a **single object parameter** — the only shape the compiler would
  enforce — was considered and deliberately **not** done in this slice: it touches every call site of
  a seam three slices already build on, and the mutation test buys the same protection today.
- **Stroke width is relative to the DRAWN set** (§11.3), so the same element changes width when the
  drawn set changes — notably R4's top-K versus `?ew=all` (a pair at 3.06 px among the top 12 is
  4.74 px across all 40). Recorded here **and beside D2**, where the top-K budget is chosen, so an
  operator never reads a width change as a data change.

**Added by R3, 2026-09-14 (§12):**

- **A column pinned over ANOTHER column drags its stubs through that column's tiles.** The same
  no-reflow consequence R2 records for the bus, now for the infrastructure band: a pin takes a
  column's devices with it, and a stub then descends through whatever lies beneath. **The evidence
  matters more than the limit.** The widened crossing assertion passed at the one coordinate first
  sampled (`x=900`) — and that was a **lattice coincidence, not a property**: the device pitch
  (`DEVICE_W + DEVICE_GAP_X` = 168) and the tile pitch (`TILE_W + TILE_GAP_X` = 172) differ, so the
  ~8-unit window in which a moved device centre lands between two tiles **slides** as the pin moves.
  A sweep of `x ∈ [140, 200]` at `y=620` violates at **13 of 16** positions, and the tester's own
  first construction missed for exactly this reason and had to be aimed by arithmetic (pin the
  single-device `site:10.20` column to x=710 → its stub at 972, inside `10.80.72.0/24`'s span
  894–1050). **Both a violating and a clean coordinate are pinned as tests**, with the delete-me
  failure message, and the reason is in `infra.ts`'s docstring. Generalized as **LES-305**.
- **Headroom is ZERO spare slots, not one.** `devicesAcross(COLUMN_W = 524)` is **3**, and the
  busiest column on **both** real bundles holds exactly 3 — so a single added device in that column
  wraps, grows the band, and moves every tile on the plane. An earlier draft of this note claimed
  "four fit across", which was **wrong**, and it was the claim that made §12.3's epoch bump sound
  harmless. The relation is now pinned in `layout.real.test.ts`
  (`max(perColumn) === devicesAcross(columnW)`), so it fails the day it stops holding — the same day
  the plane moves. Generalized as **LES-306**.
- **Three device rows lose the stub clearance.** Two rows are guaranteed clear — the alternate-row
  offset is `PITCH/2 = 84 > DEVICE_W/2 = 75`, independent of column width — but at three rows, rows
  1 and 3 share a parity and a stub can descend through the device below. Carried as a **measured**
  limit with a delete-me assertion rather than fixed: reaching it needs 5+ devices in one column
  against a real maximum of 3.
- **An unplaced device no longer overlaps a placed one** — fixed **structurally** rather than
  asserted: `infraBandRows()` gives unplaced devices their own row below every column's, with
  placement top-aligned to one shared `gridTop`. The cost, stated: a twin *with* an unplaced device
  draws its tiles one row lower than it would have. No shipped bundle has one, so no golden moved.

**Added by R4, 2026-09-15:**

- **An arc DROPS to the band top before it curves — a departure from §3 R4's implementation sketch,
  forced by measurement.** The slice text specifies "a cubic Bézier from `(tileA.centreX,
  siteA.box.bottom)` to `(tileB.centreX, siteB.box.bottom)`", and built literally that **crosses
  tiles on the real bundle**: a cubic reaches only **three quarters** of the way to its control
  points (`0.125·(y₀+y₃) + 0.75·y_c`), and site columns differ in height, so an arc between two
  *short* columns sags through the lowest tiles of any *tall* column between them. Measured on the
  pit bundle before the fix: **1 crossing among the top 12 and 6 under `?ew=all`**, all six through
  `10.80.79.0/24`. Each arc now drops vertically from its anchor to the band top and curves from
  there, which puts the whole curve in the band — where no tile is — by construction rather than by
  luck. **The §4.1 ruling is honoured in substance**: every arc still *originates at its group's
  bottom edge* (asserted for all 40 on the real bundle), and the drop is inside its own column's
  x-span, which no other column occupies. What changed is the path between the two anchors, not
  where the arc is anchored.
- **The control depth is measured from `max(bandTop, both endpoints)`, not from `bandTop` alone**
  (implementation call). On the pure layout the two are identical — a column's box bottom is always
  above the band top — so this is invisible on every shipped bundle. They differ only when a **pin**
  drags a group *below* the band, and there the literal form puts the control points above both
  endpoints and bulges the curve back **up through the tiles of the very column that was dragged**:
  the §2 F4 defect, re-entering through the geometry that retired it.
- **The top-K budget is INERT on today's content** — see the note added to §5 D2, which is where the
  budget is chosen. Recorded here too because it is the kind of fact that reads as a bug later: the
  band draws 40 arcs on the pit bundle, and no `show all` control appears.
- **A pinned column drags its arcs across another column's tiles** — the same no-reflow consequence
  R2 records for the bus and R3 for the stubs, now for the third connector family. Swept rather than
  sampled (LES-305), and **the grid is quoted with the count** so the figure is checkable: over
  `x ∈ [140, 1100]` step 20 × `y ∈ {620, 484, 300, 100, 24, lowestTileBottom + 40}` (294 positions
  per column), `site:10.20` crosses at **129** and `site:10.80` at **85**. A violating coordinate
  (`site:10.20` at 400,620) and a clean one (at 140,620) are both pinned as tests, so neither reads
  as the rule. **An independent re-run over a different (coarser) grid with an arcs-only filter
  measured 146 and 84** — the same property, different numbers, which is precisely why the grid is
  quoted with the count instead of the count travelling alone (LES-305's companion note); both
  pinned coordinates reproduce under both measurements.


<!-- original lines 532-1371 of WORK_ORDER_console_level0_rendering.md -->
## 9. R5 + R7 built as, 2026-09-11

*Iteration 1 of the ten-iteration Console run. Three fix rounds. Authority:
**implementation (Claude Code)** for everything in this section except D3 and D5, which are the
user's. Full record: CHANGELOG 2026-09-11 "Console level-0 R5 + R7 BUILT".*

### 9.1 R5 as shipped — two field names changed from §3

`CastSummaryDTO` gains **`conduit_roster: [{from, to}]`** and **`routers: [{name, segments}]`**, both
**absent** (not `[]`) from a server that does not publish them. Two deliberate departures from the
slice text, each an implementation call:

- **`conduit_roster`, not `conduits`.** `conduits` is already the shipped **count** field on this DTO;
  reusing the name would have been a breaking change to a published field for no gain.
  `len(conduit_roster) == conduits` on every bundle, and the roster is in the config's own order.
- **`routers: [{name, segments}]`, not `router_segments: [str]`.** One entry per **expanded** router,
  named as deployed (`gw01`, `gw02`), read from the expander's own ids. A twin with two gateways has
  two distinct leg sets, and a flat segment list cannot say which gateway reaches what — the WAN label
  R2 will draw needs exactly that distinction.

On the SPA side `LIVE_CONDUITS`/`conduitsAbsent` are gone; `views/useGraph.ts` hands both fields to
`buildGraph` as published, and **absent stays distinct from empty**: `graph.castOmits` lets the UI say
*this server didn't publish the roster* rather than *the twin declares none*. The WAN label reads
`gateway · N leg(s) · SNAT` for exactly one router (**N counts only legs on drawn tiles**),
`gateway · SNAT` when none of its legs is drawn, and `K routers · legs on M segments` for several.
GAP-079 is ✅ Resolved.

*A third roster joined these two on 2026-09-15* (`docs/WORK_ORDER_console_canvas.md` §18.1, Console
S5.1.a — CHANGELOG 2026-09-15): **`segment_roster: [{name, cidr, zone_type}]`**, on exactly the rules
above — additive beside the existing `segments` count (`len(segment_roster) == segments`), in the
config's own order, and **absent** rather than `[]` from a server that does not publish it, which the
Console reads as *not published by this server*. `cidr` is the **deployed** network — relocated on a
`--relocate` cast, so the Console joins it to the export by **name** — and `zone_type` is `null` where
the config declares none, never the effective zone. It is what lets the site `+` tell a segment the
cast declares from one it does not, and what makes the inspector's deployed-CIDR and zone facts true
on real bundles (only a test fixture had supplied them before).

### 9.2 R7's rule — one addressing id, one guard, everything else warned

**The canonical id — directory name = `twin_config.id` = the URL id — is the ONE addressing id.**
Every other id-shaped field is classified by a single question: *may a legitimate producer write a
value different from it?*

| field | may differ? | rule |
|---|---|---|
| directory name | — | **is** the addressing id |
| `twin_config.id` | no | **refuse** (a disagreement serves one twin's content under another's URL) |
| a **normalized** sidecar's `imprint_id` | no — only a copy-paste writes it differently | **refuse** a different value (LES-195); **warn** when missing |
| a **canonical** sidecar | declares no imprint id | nothing to compare |
| the topology export's `imprint_id` | **yes** — a cast export bakes the observed capture's id | **warn**, never refuse |
| `pit_id` (export or sidecar) | **yes** — it changes every capture | published, never compared |

The classification lives in `axrange/api/imprints.py`'s module docstring, field by field with its
reason (LES-278 generalizes it, reconciling LES-195 with LES-222). **One guard**
(`check_imprint_addressable`) runs on **every** imprint route — `/cast`, `/provenance`, `/topology`,
`/assertions`, `/layout`, apply and diff — so a bundle one route refuses, every route refuses, and a
deploy never depends on the health of a sidecar or export it does not read. Two corrections to §2's
F9 went into the record: the refusal was a **400, not a 500**, and it hit **every** cast bundle
(LES-281 — the normalized sidecar's precondition was being applied to canonical sidecars).

### 9.3 The headline bug (M2) — the API resolved a twin config's relative paths against a tempfile

Lifting the refusal exposed what had been sitting behind it (LES-276). The API materializes a twin
config to a temp IR file and then resolved the document's own relative `scripts/…` paths against the
**tempfile's** directory, so **plan build 400'd on every real cast bundle** and "deploy as twin" was
dead on real content. The ordinary `config:` API path had the same bug. The **CLI had fixed it
locally long ago** — the LES-037 family: a shared step fixed at one caller and re-derived at the
others. Now one shared `twin_config_source_dir()` / `resolve_twin_config_path()` in
`axrange/twinconfig/registry.py`, used by the CLI and both API resolvers. **Real-content proof,
pinned in tests:** plan previews of **695 / 1077 / 1227** ops (`corpapcn-golden` / `corpapcn-twin` /
`pit-395a61f04e-1780405200`) and **72** for `config: svc-verify`.

### 9.4 Robustness, as a class rather than a list of crashes

- **`axrange/jsonnum.py` (new)** — ONE bounded-number rule for anything read from a bundle artifact
  and republished: whole numbers `0 .. 2**53 - 1`, finite floats, **refusal naming the field, never
  clamped, never parsed out of a string**. It replaces three drifting copies (one accepted any-size
  ints, one any `float()`-able string, none had an upper bound). LES-279/LES-280.
- **Shape-first sidecar preconditions**, with a new **`unrecognized`** shape meaning *present but not
  consumed* — distinct from `absent`, because they are different operator situations.
- **400, not 500**, on mistyped nested export values.
- **One confidence classifier** for both the rows and the mean: a non-numeric confidence is **absent
  and counted**, which retires a typed 400 on `/provenance` (LES-286).
- The materialized IR tempfile is **deleted** on the plan and deploy paths. (The CLI's
  `--config` path still leaks its own — pre-existing, `docs/PRODUCT_GAPS.md` GAP-085.)
- Final round: the cached twin-config model is handed out as a **per-caller deep copy, on the hit path
  as well as the miss** (4.3 ms on the pit bundle / 1.6 ms on `corpapcn-twin`, against ~210 ms / ~65 ms
  to parse). `frozen=True` was **not available** — `axrange/twinconfig/expand.py:677` assigns
  `leg.ip` in place during IP allocation — and would not have covered `tc.nodes.pop()` or a nested
  model's field being rewritten anyway, since `frozen` blocks attribute assignment only (LES-284,
  LES-287). **Six** further fidelity-reader fields were typed in the same round — `segments[i].entities`,
  `segments[i].coverage_source`, `nodes[i].role`, `nodes[i].role_evidence_class`,
  `nodes[i].segment_evidence_class`, `nodes[i].locality` — `role` being the live hazard (a dict key in
  `fidelity/rows.py`'s `_group_key`, so a list or dict `role` was the same unhashable-key 500 already
  fixed for `segment`/`cidr`): the class, not the one field that crashed (LES-285).

### 9.5 Performance — the guard's cost, measured and paid back

The new guard made **every** route parse the twin config: **214.7 ms** on the pit bundle, paid on
every pin drop. A **stat-keyed parse cache** at the single loader (key: resolved path + inode + size +
mtime + ctime; **stat before read**; failed parses never cached; bounded and locked, because sync
FastAPI routes run on a threadpool), with a per-caller deep copy on top:

| measure | before | final |
|---|---|---|
| addressing guard, pit bundle | 214.7 ms | **4.48 ms** |
| addressing guard, `corpapcn-twin` | 65.2 ms | **1.68 ms** |
| `GET /layout`, pit (every pin drop) | 223.5 ms | **9.2 ms** |
| `GET /assertions`, pit | 244.2 ms | **9.6 ms** |
| Console open | 2522 ms, 8 parses | **637 ms, 0 parses** |
| plan preview | 2234 ms, 3 parses | **1737 ms, 1 parse** |

So the guard is **~48× cheaper than the parse it replaces**, and the residual ~4.5 ms is the deep copy
that buys isolation between callers. The **cold first parse per bundle per process is unchanged** —
stated rather than averaged away. LES-282/LES-283/LES-284.

### 9.6 Gates

**Final: pytest 3089 passed / 12 skipped / 0 failed (3101 collected); Vitest 325 passed / 8 skipped
(333);** `npm run typecheck` clean from zero; `npm run build` exit 0; the seven layout coordinate
goldens **byte-identical** (neither slice is layout). **15 revert proofs** across the three fix
rounds. `doc-stats.py --check` clean. No live-verify — nothing here deploys or touches a cluster; the
standing `axrange serve` was deliberately **not** restarted (GAP-084).

### 9.7 What this slice deliberately did not do

R5 publishes the roster; it does **not** change how the band is drawn — `lanes.ts` still draws lanes
and **R4 retires it** for arcs (§4.1). R7 makes the routes agree on an id; it does **not** touch the
Grafana plugin's imprints screen, which now renders unmeasured values as measured zeros precisely
because the refusal no longer stops it loading (`docs/PRODUCT_GAPS.md` GAP-083) — the user's **D5**
ruling retires that screen at S7 rather than patching it
(`docs/WORK_ORDER_console_canvas.md` §8.1). Ten further honest limits and findings are registered as
GAP-083..GAP-092.

## 10. R1 built as, 2026-09-12

*Iteration 2 of the ten-iteration Console run. **Frontend-only** — everything below is under
`o2/console/`; no Python was touched and the policed counters do not move. Authority:
**implementation (Claude Code)** throughout (the run's scope and order are the user's). Full record:
CHANGELOG 2026-09-12 "Console level-0 R1 BUILT".*

### 10.1 Width — one pure module, a 0.8 floor, and a readout that says what is cut

`src/layout/viewport.ts` (**new**, pure — no React, no DOM) is the width rule in one place:
`BASE_SCALE = 1` (one layout unit = one CSS px, the default, with the pane scrolling in both axes),
`MIN_FIT_SCALE = 0.8` as the floor a *fit width* may never cross, `fitScale`/`scaleFor` as the only
arithmetic either mode uses, and `estimateMapWidth` as the jsdom / first-paint fallback that mirrors
`shell.css`'s grid minimums. It **never scales up** (a six-tile twin is drawn at 1:1, not magnified
to fill the pane) and an unmeasurable pane yields `BASE_SCALE` rather than a shrunken drawing.

`theme/shell.css`'s `svg.level0` drops `max-width:100%; height:auto` and takes a **12 px** root font
— that pairing is what makes the floor a *legibility guarantee*: 12 px × 0.8 = 9.6 px is the smallest
type the map can ever draw. **Level 1 and the matrix keep the old fit-to-pane rule**, deliberately:
neither has level 0's plane width.

`views/Level0.tsx` measures the pane (a guarded `ResizeObserver` **plus** the window listener — the
pane changes width with no window resize at all when the outline collapses, LES-291) and sizes the
SVG `width`/`height` = units × scale, publishing the applied scale as `data-scale`. **The viewBox
stays in pure layout units**, so the scale is a view concern that cannot perturb the coordinate
goldens (LES-268) and the S4 pin drag keeps reading its scale off the live CTM. The `1:1 | fit width`
control carries a readout that states what is cut rather than leaving the scrollbar as the only tell:
`· N px off-screen — scroll, or fit width`, and `· 80% floor — scroll for the rest` when fit itself
cannot show the whole plane.

### 10.2 Rails — built by mapping `TIERS`, with content-derived heights

`geometry.ts` gains `RAIL_W = 128` / `RAIL_GAP_X = 16`; `tiers.ts` gains `TIER_LABELS`
(`INTERNET / WAN EDGE / INFRASTRUCTURE / SEGMENTS / EAST-WEST`), beside `TIERS` because the ordered
vocabulary and its words are one thing; `level0.ts` gains `Level0Layout.rails: TierRail[]`,
**built by mapping `TIERS`** so "in `TIERS` order" is structural rather than a convention a later
edit could break. The content origin moves right by `RAIL_W` — one fixed left margin, no relative
geometry changed — and the top chrome now centres over the **content** area rather than the whole
canvas, or the gutter's asymmetry would pull the Internet pill left onto the rails.

**Rail heights are content-derived**, which is the honesty call in this slice: the three fixed bands
take their stacked extent *only when they hold something*, and the two data tiers take the extent of
what they actually hold (the columns; the conduit band). A cast with no router draws **no** WAN EDGE
rail and one with no infrastructure devices draws **no** INFRASTRUCTURE rail — a band's *reserved*
height is not evidence that anything is in it, and a label over nothing claims the tier has content.
A zero-height rail is still **emitted** (the rail set is a total function of the tier vocabulary); the
view filters it. Rails are **pre-pin** extents: `applyPins` deliberately does not move them, since a
rail that chased a dragged tile would stop describing its tier.

### 10.3 Chrome — the summary bar and the outline give the drawing its pane back

The summary strip (`CastNumbers` + `EvidenceMix`, an 8-tile grid that cost the top half of the pane
before the drawing began) collapses to a **one-line bar with a digest**, expanded by `?summary=1`
**in the URL, not a store** — so it survives a reload and rides the one navigation path every drill
already threads. The outline pane collapses too, in `localStorage` with **every access guarded** (it
is a per-viewer convenience about this browser's window, not a description of what is being looked
at, so a pasted link must not carry it), and is **forced open at the catalog level** where it is the
only navigation — without touching the stored preference, and with the toggle *hidden* rather than
left as a control that visibly does nothing. `clear` no longer collapses the summary.

### 10.4 One filter taxonomy (and a NUL byte)

`model/zoom.ts` now states the three categories **once** — **dimming filters** (`ghosts` / `class` /
`occupancy_lt`), the **emphasis flag** (`changed`), **view flags** (`summary`) — and publishes
`anyDimmingFilter` + `clearFilters`; `model/graph.ts` and `views/Filters.tsx` derive from them
instead of restating membership at three sites, which is exactly how a new flag joins the wrong set
(LES-288). Consequences the taxonomy buys: opening the summary bar dims no tile, `clear` drops the
dimming filters and the emphasis flag while preserving view flags, and the transient selection reset
is keyed on the **scope-bearing** part of the URL only — so opening the bar no longer closes an open
drawer or matrix-cell selection. `parseFilters`'s three copy-pasted boolean readers collapse into one
`readFlag`.

Also: an SVG `<desc>` names the drawn tiers for assistive tech (the rail groups are `aria-hidden`, or
forty tiles' worth of rail labels would be read in document order); and a literal **NUL byte** in
`model/graph.ts` — a map-key separator committed long before this slice — is rewritten as an escape,
because `file` classified the module as binary data and GNU grep had been silently skipping it, which
cost this slice a grep (LES-295).

### 10.5 The goldens, accounted for

Regenerated **once, in this slice**, and only **two of the seven**: `level0.golden.json` and
`level0-added-column.golden.json`. Both trees were **machine-walked** rather than eyeballed — **31 and
35 changed leaves, every one an `x` or a `width`, every one exactly +128 (`RAIL_W`); no `y` changed
anywhere; `rails` is the only new key.** The other five goldens are byte-identical. That walk is the
evidence that the rail gutter is a pure translation of the content plane and not a relayout — the
property a diff of two large JSON trees cannot be trusted to show by inspection.

### 10.6 Gates

**Vitest 398 passed / 8 skipped** (from 325/8 at iteration 1: R1 added tests, the bounded re-test
added 34, the fix round 12); `npm run typecheck` clean from zero; `npm run build` exit 0;
`pytest -m "not slow"` **3083 passed / 12 skipped / 0 failed, byte-identical before and after** —
nothing server-side moved. **Ten revert proofs red.** `doc-stats.py --check` clean. No live-verify:
nothing here deploys or touches a cluster, and the standing `axrange serve` is still deliberately
**not** restarted (GAP-084).

### 10.7 What this slice deliberately did not do

No trunk (R2), no infra stubs or role badges (R3), no arcs (R4 — `lanes.ts` still draws lanes), no
tile content or per-group budgets (R6). Three honest limits are recorded in **§8** rather than fixed
here, and one of them carries a decision forward: **R2 adds the plane/epoch stamp to `axr-layout/1`**
that makes an absolute pin survive a moving plane.

## 11. R2 built as, 2026-09-14

*Iteration 3 of the ten-iteration Console run. Authority: **implementation (Claude Code)** throughout
(the run's scope and order are the user's). Full record: CHANGELOG 2026-09-14 "Console level-0 R2
BUILT". **Almost entirely frontend** — the one Python change is the additive, opaque `plane` field on
`axr-layout/1`, which is why the pytest counter moves 3101 → **3112 collected** while op types (40)
and catalog rules (114) do not.*

### 11.1 The path — one pure module, and a leg that ends at the NEAREST edge

`src/layout/trunk.ts` (**new**, pure — no React, no DOM, no graph) takes four boxes' worth of
already-placed chrome (the Internet pill, the WAN edge, each site header) plus each group's
north-south volume and returns orthogonal polylines: a **spine** (Internet → WAN, carrying the tap),
a **stem**, a horizontal **bus**, and **one vertical leg per site group**. Three calls worth naming:

- **A leg ends at its group header's NEAREST horizontal edge and enters no box.** Normally that is
  the top edge; a group a pin has dragged *above* the bus is approached from below and stops at its
  **bottom** edge instead, because ending at the top would run the leg straight through the header it
  is pointing at (review S1). The claim is checked as computed geometry (§11.5), not looked at.
- **The bus sits inside the existing WAN→infrastructure tier gap**, so the trunk **adds no height and
  moves no tile** — which is also why it invalidates no pin (§11.4). The legs do cross the
  infrastructure band, where R1's device row is still centred; they are drawn beneath it, so a device
  reads as sitting *on* the trunk rather than being struck through, and **R3** is what settles that
  band properly.
- **Every group gets a leg, including a silent one** — a hairline, never nothing. "No traffic was
  observed here in this window" and "this group is not attached to the edge" are different
  statements, and only the first is ours to make.

`layout/level0.ts` gains `trunk` and `tap`, and **the WAN box is now keyed on `graph.wanEdge`**
rather than on `routerSegmentNames.length > 0` — a *disjunction* (a router was published **or**
crossing traffic was observed) where the old rule was a necessary condition, which is why every test
that encoded the old rule had to be **restated rather than deleted** (LES-299): the rails test that
emptied the WAN tier by dropping routers now has to drop the non-local population and its edges too,
and the case it used to cover is the inferred case, which has its own coverage.

`model/graph.ts` supplies the data: `GraphSegment.northSouth: {bytes, flows}` over edges with
**exactly one known-local end** (both ends must be known nodes), `Site.northSouth` summed in
`layout/sites.ts`, and `ConsoleGraph.wanEdge` / `.vantage`. An edge with no **published** router is
`inferred: true` and labelled ***egress (inferred from observed traffic)***, never "gateway".
**`inferred` keys on publication, not on legs** — a cast that declares a legless `gw` has declared a
gateway, and calling that inferred would credit the observation for something the twin states.
`applyPins` re-runs the trunk over **moved** headers (the spine and tap are chrome and do not move),
so a dragged group's leg tracks it for free — the same re-anchoring rule LES-266 established.

### 11.2 The tap badge — verbatim, because the two real bundles disagree

`Level0Layout.tap` carries the export's own `vantage` and `windows` and the view draws them
**verbatim, beside** the badge rather than inside it. The pit bundle says
`axellio-hq/corp-apcn-rdr1:12` over 9 windows; the golden says `https://10.80.65.7:0-11` over 11. A
parser would have to guess which part is the host and which the window count, and would be wrong on
one of the two bundles that actually ship — so R2 displays the producer's string and says how many
windows it covers, and the §8 "per window set, never a rate" rule is what the label states.

### 11.3 The scale — normalised over the DRAWN set's own range (decided here; R4 inherits it)

`src/layout/scale.ts` (**new**, pure) holds `1 + 5 · normalised log10(bytes)` once, because **R4's
arcs reuse it verbatim** — which is also why the normalisation range had to be settled in this slice
rather than R4's. The decision: **normalise over the drawn set's own log range** (`[min .. max]` of
the positive values actually present), not against an absolute zero.

Measured, on real data, before committing to it:

| drawing | against `log10(1+max)` alone | against the set's own range |
|---|---|---|
| pit bundle, site legs | 6.00 / 5.82 / 4.57 | **6.00 / 5.38 / 1.00** |
| twin bundle, site legs | — | **6.00 / 5.08 / 1.00** |
| R4's top-12 east-west pairs | 5.05 … 4.39 (**0.66 px** of spread) | 3.06 … 1.00 (**2.06 px**) |

Normalising against an absolute zero spends most of the 1..6 band on magnitudes no twin contains, so
§1 property 3 ("magnitude visible before any label") would have failed across eleven of twelve arcs.
Two consequences are stated rather than hidden: **width is relative within one drawing** (§8, and the
D2 note), and **zero is out of band by construction** — `HAIRLINE` (0.5) is the *claim*, while the
view marks a silent element `.trunk.is-silent` and the stylesheet's `stroke-width: 1` (dashed)
overrides the presentation attribute, because half a pixel at 50 % opacity is not a claim anyone can
see. So a test reading the attribute sees `0.5` and the pixels say 1; a test asserting the width
asserts the class beside it. The same treatment covers a declared-but-silent spine, stem and bus.

### 11.4 The plane stamp — R1's deferred decision, and the write half that took three rounds

`src/layout/plane.ts` (**new**, pure) mints `plane: "l0-<epoch>-<fnv1a8>"`:

- the **hash** is taken over `PLANE_CONSTANTS` — *exactly* the constants that decide where a
  **pinnable** element lands (the content origin incl. R1's `RAIL_W` gutter, the tier stack above the
  tiles, the site/tile grid) — so a slice that edits one cannot forget to bump anything. R2 adds a
  tap badge and a bus **without moving a single tile**, so **R2 invalidates no pin and the stamp must
  not say it does**;
- the **epoch** is the manual half, for a plane change no constant expresses. Its scope is wider than
  the hash's and this is the whole of it: **R6's per-group budgets** change which tiles exist, and
  `sites.ts`'s bucketing / `misc` merge and `model/keys.ts`'s `compareCidr` decide which group a tile
  is in and in what order — **a hash over numbers cannot see a change in a comparator**. Bump the
  epoch in the slice that changes either, and say so here.

The server (`axrange/api/console_files.py`, `dtos.py`, `service.py`) round-trips the stamp as an
**opaque token** — shape-checked only (≤64 chars from `[A-Za-z0-9._:-]`, bounded because a browser
writes this file into a git-tracked bundle) and **never interpreted**, since the plane is a property
of the code that draws it and a server deciding whether a stamp is current would be a second opinion
about a layout it does not draw. **Absent stays absent**: a legacy file read and re-saved unchanged
is byte-identical, `plane: null` is never invented, and an empty string is refused with the reason
(*omit the key to mean "no stamp"*). Status is `none` / `current` / `legacy` / `stale`, and legacy
and stale pins are **accepted and flagged** — dropping them silently destroys work the operator can
still recover by dragging, and drawing them without a word is the corruption the stamp exists to stop.

**The write half is where the danger was.** A drag commits the **whole** pin set, so stamping the
document current on every save re-labels N inherited pins as "authored against this plane" off one
gesture: the warning disappears for ever while N−1 pins are silently wrong — LES-289's own corruption
re-entering through its own mechanism (review must-fix M1). `planeForSave(pins, authored,
currentPlane)` is the fix, **one pure rule living beside the status reader it has to agree with**: the
stamp advances only when the set is **wholly** re-authored (a reset, vacuously, or every surviving pin
re-placed); otherwise the document keeps the stamp it arrived with, so a legacy file stays legacy
until the operator finishes the job. The final review round found the remaining hole: the session
record was keyed by pin **name**, so `stale → drag A → 412 → reload (someone else's A) → drag B`
made the key set cover the document and **certified a pin nobody re-placed**. Re-authorship is now
identified **by value** — the record says what *this session wrote*, and a key counts only while the
pin's current coordinates still equal it (an exact match: both sides are numbers we computed). The
record moved into `ImprintWorkspace`, beside the document it describes — it previously died on every
drill to level 1 — and `pinPlaneNote` counts progress ("re-placed 1 of 2") with the same rule that
clears the flag, so the advice and the guard cannot drift (LES-296).

### 11.5 The goldens and the crossing check, accounted for

**`noConnectorCrossesATile` (§1 property 2) is now a computed assertion**, in
`o2/console/test/layout/helpers.ts` and called by **every** level-0 golden from R2 on. Liang–Barsky
against the closed box **plus a strict-interior midpoint test**, so a connector that *ends* on an
edge (a leg stopping at a header) or runs *along* one is correctly not a crossing; it returns the
**named offenders** rather than a boolean, so a failure reads `trunk leg site:10.80 crosses tile
10.80.72.0/24`. It covers the trunk **and today's lanes**; R3's stubs and R4's arcs join it as they
land, and the infra tie-lines are deliberately absent because the view derives them and R3 replaces
them with stubs the layout will publish.

**The goldens: cumulative versus pre-R2, 0 leaves changed and 0 removed — every difference is an
addition.** `level0.golden.json` +80 leaves, `level0-added-column.golden.json` +99 (`trunk`, `tap`,
`northSouth`), **three level-1 goldens +2 each** because `layout1` embeds `GraphSegment` and that
type now carries `northSouth`, and **both matrices byte-identical**. Which goldens a model field
moves is decided by what the layouts **embed**, not by which layout file was edited (LES-300). The
final fix round moved no golden at all.

### 11.6 Gates

**Vitest 475 passed / 8 skipped** (from 398/8 at iteration 2); `npm run typecheck` clean from zero;
`npm run build` exit 0; **full `pytest -q` 3100 passed / 12 skipped (3112 collected)**. **7 revert
proofs plus 3 for the fix rounds**, each RED and each naming the test it protects; the save seam's
mutation fails **exactly 1 of 475**. `doc-stats.py --check` clean. No live-verify — nothing here
deploys or touches a cluster, and the standing `axrange serve` is still deliberately **not**
restarted (`docs/PRODUCT_GAPS.md` GAP-084).

### 11.7 What this slice deliberately did not do

No infra stubs or role badges (**R3**, iteration 4 — the infrastructure band is still R1's centred
row with tie-lines, which is why the legs are drawn beneath it), no arcs (**R4** — `lanes.ts` still
draws lanes and inherits `scale.ts` when it is retired), no tile content or per-group budgets
(**R6**, which is also the first slice expected to **bump `PLANE_EPOCH`**). Three honest limits are
recorded in **§8** rather than fixed here. *(R3 turned out to bump the epoch first — see §12.3 for
why, and for the rule that leaves R6.)*

## 12. R3 built as, 2026-09-14

*Iteration 4 of the ten-iteration Console run. **Frontend-only** — everything below is under
`o2/console/`; no Python was touched and the policed counters do not move (pytest **3112
collected**, op types **40**, catalog rules **114**). Authority: **implementation (Claude Code)**
throughout (the run's scope and order are the user's). Full record: CHANGELOG 2026-09-14 "Console
level-0 R3 BUILT".*

### 12.1 The infrastructure band — each device over its own group, and a stub instead of a diagonal

`src/layout/infra.ts` (**new**, pure — no React, no DOM, no graph) is the whole of F3's fix, and the
fix is **positional rather than cosmetic**: a device's x becomes the **centre of the site column
that holds its segment**, so it is already over the group it belongs to, and the connector collapses
to a short orthogonal **stub** down to that column's header. Nothing has to be routed around because
nothing is in the way — the header spans the column's full width, so any device inside that width
drops straight onto it. That is the entire reason the module moves devices at all.

It is modelled on R2's `trunk.ts` deliberately (ids in, boxes and polylines out), which is what lets
`applyPins` **re-run it over moved columns** and get devices that track a dragged group for free —
LES-266's re-anchoring rule, now on its third consumer. `layout/level0.ts` places devices through it,
renames `DevicePlacement.tieToCidr` → **`siteKey`** (the field now names a column, not a tile), and
publishes **`Level0Layout.infraStubs`**. Three calls worth naming:

- **A stub ends at its header's NEAREST horizontal edge and enters no box.** That is the rule R2's
  legs already needed, so it moved into `geometry.nearestEdgeY` and is now shared rather than
  written twice: normally the device is above and the stub stops at the header's top; a group a pin
  has dragged *above* the band is approached from below.
- **An unplaced device stays centred and gets NO stub.** There is no group to point at, and a stub
  to the nearest column would assert a membership the export does not support; the view keeps the
  dashed outline so "not placed" is visible rather than implied. It also gets its **own row** below
  every column's (`infraBandRows`, with placement top-aligned to one shared `gridTop`), which is how
  the old overlap-with-a-placed-device case is fixed *structurally* instead of being asserted away
  (§8).
- **The band's height is content-derived.** `TIER_INFRA_H` became a **floor**: a column whose devices
  overflow its width wraps to a second row and the band grows to hold it, rather than overlapping the
  tier beneath. A wrapped column uses one slot fewer per row and offsets alternate rows by half a
  slot, which keeps a stub from descending through the device below it — exact at two rows, and
  lapsing at three (§8, measured rather than claimed away).

**Measured on the shipped pit bundle:** the seven devices distribute **2 / 3 / 2** — two resolvers
over 10.20, both domain controllers plus `10.80.64.2` over 10.80, two singleton-site resolvers over
`misc` — and **no connector touches a tile**. The stubs join `noConnectorCrossesATile` (§11.5), which
they could not before: R2 left the tie-lines out of that helper precisely because the **view**
derived them and the layout published nothing to check (LES-304).

### 12.2 The badges — a projection, and one that says so

`GraphSegment.roleBadges` carries up to three buckets of the existing `roleHistogram` — derived, not
recounted, so the two can never disagree about what a segment holds (LES-025) — drawn as `DC 2` /
`EP 14`. `model/roles.ts` gains `ROLE_BADGE_MAX`, a display-only abbreviation map and a **total**
`roleBadgeLabel()`: an unknown role derives initials (`ot-historian` → `OTH`) or a four-glyph stem
(`hypervisor` → `HYPE`) rather than being dropped, and an empty `role: ""` an export wrote through
falls back to the unroled label rather than rendering a bare count. `RoleHistogram` is **removed**
from `mapParts.tsx` — the tile was its only caller; `Outline.tsx` still renders the model's full
histogram, which remains the record.

**Why the bar had to go, rather than just shrink:** at 140 units across a 156-unit tile, a
three-role mix is three coloured slivers that say *mixed* and nothing more, with the roles
themselves reachable only through a hover title. The hue is kept (same `roleColor`, same rank key),
so a role still reads as the same colour wherever it appears — the abbreviation gains a legend the
bar never had.

**The ranking rule is a recorded departure from §3 R3's own wording**, because that slice's rule and
its acceptance cannot both hold on the data that ships (the full ruling, the measured cost and the
refusal to widen to four badges are in the §3 R3 correction blockquote; LES-303). What matters here
is the consequence for the drawing: since the badge set is deliberately **not** a ranking, three
badges must never read as the complete answer. The tile therefore discloses the projection **twice,
from one helper** (`badgeRowTitle`, so the two phrasings cannot drift): a `<title>` on the badge row
(*"3 roles shown of 5 on this segment — +2 more in the inspector"*) and *", +2 more roles"* appended
to the tile's own `aria-label`. The badge group itself is `aria-hidden`, like the evidence ring —
assistive tech gets one sentence rather than three interleaved title nodes in document order
(LES-307).

### 12.3 `PLANE_EPOCH` 1 → 2, and the rule that leaves for R6

R2 designed the stamp so a slice that moves a **pinnable** element cannot forget to invalidate a pin:
the hash is taken over exactly the constants that position one. **R3 is the case that hash cannot
see.** No constant moved — the band's height became a *function of the content* (§12.1), and a hash
over numbers cannot see a rule that reads the data, which is the same argument `sites.ts`'s bucketing
and `compareCidr`'s ordering already get. So the manual epoch is the only carrier, and it goes to
**2**.

The cost is stated rather than hidden: **no bundle in the tree wraps**, so R2's plane and R3's plane
are identical for every bundle that ships, and the bump makes the app say *"these pins may no longer
line up"* where in fact they do. That is the **safe** direction of a question one global token cannot
answer per-bundle — a false flag is a sentence the operator dismisses; a missing one is the silent
misplacement the stamp exists to stop (LES-289) — and blast radius is zero today, since the only
`layout.json` in the tree holds `"pins": {}` and pins are accepted and flagged either way, never
dropped.

**The rule this establishes for R6** (and for anything after it): **any geometry that reads the data
rather than only the constants bumps the epoch.** R6 was already expected to bump it for the
per-group budgets; it now inherits a precedent rather than a first case.

### 12.4 The goldens, accounted for

Regenerated **once**, in the build round, and **not at all** in the fix round. Machine-walked rather
than eyeballed:

| golden | changed | added | removed |
|---|---|---|---|
| `level0.golden.json` | 18 | 69 | 7 |
| `level0-added-column.golden.json` | 18 | 77 | 7 |

**Of the 18 changed leaves, 15 are a 3-element permutation** (the device array is now **column-major**
rather than one centred row) and **3 are `box.x`**. **No `y` moved anywhere**, and nothing outside
`infraDevices` changed — which is the evidence that a content-derived band height did not, on this
content, move the plane (§12.3's whole point). The three level-1 goldens gained `roleBadges` and
nothing else, because `layout1` embeds `GraphSegment` (LES-300, second instance); both matrix goldens
are byte-identical.

### 12.5 Gates, and the acceptance clauses

**Vitest 532 passed / 8 skipped** (from 475/8 at iteration 3); `npm run typecheck` clean from zero;
`npm run build` exit 0; `pytest -m "not slow"` **3094 passed / 12 skipped**, unchanged — no Python
was touched. **Ten revert proofs across the slice, all RED.** `doc-stats.py --check` clean.

**§3 R3's three acceptance clauses all pass**: every infra device's box x-range lies inside its site
column's x-range; `noConnectorCrossesATile` holds for the stubs (and the helper is guarded against
passing vacuously, LES-298); and the real-bundle test asserts the two domain controllers over the
10.80 column **and** the `DC 2` badge on `10.80.20.0/24` — the last of those only because of the
reservation rule, which is exactly why the departure is recorded rather than quietly applied.

Screenshots were deliberately **not** re-shot after the fix round, with the reason stated: nothing in
that round can change what is drawn, so the iteration-4 shots remain accurate. No live-verify —
nothing here deploys or touches a cluster, and the standing `axrange serve` is still deliberately
**not** restarted (`docs/PRODUCT_GAPS.md` GAP-084).

### 12.6 What this slice deliberately did not do

No arcs (**R4**, iteration 5 — `lanes.ts` still draws lanes and inherits `scale.ts` when it is
retired; the reconnaissance is done: exactly **40** east-west pairs, top pair
`10.80.20.0/24 ↔ 10.80.75.0/24` at **2.23 GB** against a **0.04 GB** runner-up, which is why R2's
log scale is what makes that band readable at all). No tile ranking, per-group budgets, byte figures
or varying tile size (**R6** — F5 is only *partly* closed here: the tile now names its roles, but
every tile is still the same size and says nothing about volume). Four honest limits are recorded in
**§8** rather than fixed, two of them measured sweeps rather than single samples.

## 13. R4 built as, 2026-09-15

*Iteration 5 of the ten-iteration Console run. **Frontend-only** — everything below is under
`o2/console/`; no Python was touched and the policed counters do not move (pytest **3094 passed / 12
skipped** under `-m "not slow"`, op types **40**, catalog rules **114**). Authority:
**implementation (Claude Code)** for the geometry, the two departures, the ACCEPT ruling on the
budget and every limit named; the **arcs ruling itself is the user's** (§4.1, 2026-09-11), and the
run's scope and order are the user's. Full record: CHANGELOG 2026-09-15 "Console level-0 R4 BUILT".*

### 13.1 The band — one pure module, two families, and a height bounded by construction

`src/layout/arcs.ts` (**new**, pure — no React, no DOM, no graph) replaces `lanes.ts`, which is
**deleted** along with its goldens and its "22 lanes" real-bundle assertion (now "22 asserted
arcs"). It takes pairs and already-placed boxes and returns curves, in the idiom R2's `trunk.ts` and
R3's `infra.ts` established — which is what lets `applyPins` re-run it over **moved** tiles and group
boxes and get arcs that track a dragged column for free (LES-266, fourth consumer).

`model/graph.ts` supplies the data: **`TrafficPair`** / **`ProtoShare`** and `trafficPairsOf`, which
aggregates the export's edges by the unordered pair of their ends' segments — **placed and local on
both ends**, same-segment excluded (that is level 1's), non-local excluded (that is R2's
north-south) — carrying `bytes`, `flows` and a `protoMix` in descending byte order. `layout/level0.ts`
publishes **`arcs`** and **`hiddenPairs`** behind a `showAllPairs` option; `geometry.ts` gains
`ARC_DEPTH_MIN = 28` / `ARC_DEPTH_MAX = 120` / `ARC_DEPTH_PER_DX = 0.05` / `ARC_BAND_PAD = 20` and
the curve's sampling constant; `model/zoom.ts` gains **`?ew=all|top`**; `theme/shell.css` swaps
`.lane` for `.arc` / `.arc.is-asserted` / `.arc.is-silent`.

**Two families share one band.** *Observed* pairs come from the edges, drawn **top-K by bytes**.
*Asserted* conduits come from the cast's own roster — **every one of them**, in the edit colour,
**dashed when the pair carried no observed traffic** — so a declared-but-silent conduit is visibly
different from an observed-but-unasserted flow. That is the single property the retired lane
geometry had that an aggregate-by-volume drawing loses (one lane, one conduit), preserved for the
set where it means something. A pair that is **both** is drawn **once, as asserted, carrying its
observed volume**: two identical curves on the same two anchors are one curve plus a misleading
count. Protocol never reaches the module — `15` §5 stands, colour on the Console is evidence — so
the mix lives in `arcTitle`'s hover text and in the inspector.

**The band's height is bounded by construction**, not by the budget: depth is clamped at
`ARC_DEPTH_MAX`, so the band is at most `ARC_DEPTH_MAX + ARC_BAND_PAD` tall **whatever the pair
count** (proven at 300 pairs), where 40 lanes cost 440 px at one 11 px channel each. That bound is
what makes §13.3's ruling on the inert budget cheap.

### 13.2 The two departures — both forced by measurement, both from the SKETCH and not from the ruling

**An arc drops to the band top before it curves.** The slice text specifies a cubic straight from
one group edge to the other, and that shape **measurably crosses tiles on real content**: a cubic
reaches only **three quarters** of the way toward its control points — its lowest point is
`0.125·(y₀+y₃) + 0.75·y_c` — and site columns differ in height (a 16-segment column is six tile
rows; a one-segment column is one), so an arc between two *short* columns sags through the lowest
tiles of any *tall* column between them. Measured before the fix: **1 crossing at K = 12 and 6 under
`?ew=all`**, all six through `10.80.79.0/24`. Each end now descends at its own tile's x — inside its
own column's x-span, which no other column occupies — to the band top, and curves from there.
**§4.1's ruling is honoured in substance**: every arc still *originates at its group's bottom edge*,
asserted for all 40 on the real bundle. What changed is the path *between* the anchors, not where
the arc is anchored. The tester re-measured **both** geometries independently (0 crossings shipped,
1/6 literal) and six tests go red without the fix.

**The control depth is anchored at `max(bandTop, both endpoints)`.** Provably identical to the
sketch on the pure layout — `bandTop = lowestTileBottom + 26` while the tallest column's bottom is
`+12` — and golden-pinned to prove it. They differ only under a **pin** that drags a group *below*
the band, where the literal form puts both controls above both endpoints and bulges the curve back
**up through the tiles of the very column that was dragged**: the §2 F4 defect, re-entering through
the geometry that retired it. Same rule as `geometry.nearestEdgeY` gives R2's legs and R3's stubs —
approach a box from the side it is actually on.

### 13.3 The budget, and the ruling that it may stay inert

K = 12 is implemented and tested. **On today's content it never fires**, because §4.1's own rule
draws every *asserted* conduit while the budget bounds only the *observed* set, and both shipped
casts declare a conduit for essentially every pair they observed: **pit, 40 conduits for 40 observed
pairs** (40 asserted arcs, `hiddenPairs === 0`); **`corpapcn-twin`, 22 against 31** (22 asserted + 9
observed, still under K). The full ruling and its two alternatives are recorded at **§5 D2**, where
the budget is chosen; in short, **budgeting by volume was rejected** because a declared-but-silent
conduit carries `bytes: 0`, sorts last, and would be hidden **first** — backwards for the very
element §4.1 exists to distinguish — and a **second budget for the asserted family was rejected** as
a second control and a second projection that would hide the twin's *declared structure*. Since the
band is height-bounded by construction, the cost is **density, not layout**.

Two consequences are stated rather than left to be discovered: the *show all* control is **not
rendered at all** on either bundle (nothing is hidden), and `?ew=all` is therefore **unreachable
through the UI** — so D2's "3.06 px among the top 12 versus 4.74 px across all 40" describes a view
no operator can currently open, reachable only by constructing the graph without the roster the API
publishes.

### 13.4 Two evidence corrections — what a test over real DATA is not

**The real-bundle budget tests described a view that cannot occur.** The pit bundle's own YAML
carries 40 conduits and the API publishes them, but those tests built the graph **without the
roster** — real data assembled through a seam the app never uses. They are **kept**, because they do
exercise the budget branch, but retitled `[no-roster construction]` and commented at the
construction itself, and a new `describe` covers the **live shape**: 40 conduits for 40 observed
pairs (0 undeclared), 40 asserted arcs, `hiddenPairs === 0`, no crossings, and `?ew=all` a **no-op
including every stroke width**. Generalized as **LES-311**.

**A pin-sweep figure is only meaningful with its grid.** §8's sweep is published with the grid it
was measured on (294 positions per column) rather than as a bare count, because an independent
re-run over a coarser grid with an arcs-only filter produced **different numbers for the same
property** (146/84 against 129/85). Both pinned coordinates reproduce under both measurements; the
grid is what makes either figure checkable.

### 13.5 The goldens, accounted for

Regenerated **twice** — once in the build round and once in the fix round — and machine-walked
rather than eyeballed both times:

| round | golden | changed | added | removed |
|---|---|---|---|---|
| build | `level0.golden.json` / `level0-added-column.golden.json` | **2** each | **58** each | **33** each |
| fix | `level0.golden.json` / `level0-added-column.golden.json` | **3 keys renamed** each | 0 | 0 |

The build round's **2 changed leaves are `height` 555 → 561.3 and `rails[4].box.h` 61 → 67.3** —
**only the east-west band grew**; the 58 added are `arcs` + `hiddenPairs` and the 33 removed are
`lanes`. The fix round renamed `arcs[i].observed` → `hasTraffic` with **0 values changed**. **No
tile, site, trunk or device coordinate moved anywhere**, which is why `PLANE_EPOCH` stays **2** and
`plane.ts` is not in the changeset: R4 invalidates no pin.

The other five goldens are **byte-identical throughout — including all three level-1 goldens**,
because `trafficPairs` hangs off `ConsoleGraph` rather than the embedded `GraphSegment`. That was
**predicted from LES-300 before the regeneration and confirmed by it**, which is the point of the
lesson: which goldens a model field moves is decided by what the layouts *embed*.

### 13.6 Gates, and the acceptance clauses

**Vitest 594 passed / 8 skipped** (581 at build, 588 after the tester's 7, 594 after the fix round);
`npm run typecheck` clean from zero; `npm run build` exit 0; `pytest -m "not slow"` **3094 passed /
12 skipped**, unchanged — no Python was touched. `doc-stats.py --check` clean.

**§3 R4's acceptance clauses hold**, with the budget clause qualified rather than claimed:
`trafficPairs.length === 40`; the top pair is `10.80.20.0/24 ↔ 10.80.75.0/24` and its arc is the
widest; **every arc's start and end y equals its group's bottom edge** (all 40); `noConnectorCrosses
ATile` holds for the arcs, which now join the trunk and the stubs in that helper; the band height is
bounded by `ARC_DEPTH_MAX + ARC_BAND_PAD` regardless of pair count (300-pair proof); a pinned tile's
arcs follow it while an unpinned neighbour's are byte-identical; and a no-edges bundle renders the
empty band with its own note, GAP-079's old note deleted. The **top-K clause is exercised through a
no-roster construction** and is inert on live content — §13.3, recorded rather than glossed.

No live-verify — nothing here deploys or touches a cluster, and the standing `axrange serve` is
still deliberately **not** restarted (`docs/PRODUCT_GAPS.md` GAP-084).

### 13.7 What this slice deliberately did not do

No tile ranking, per-group budgets, byte figures or varying tile size (**R6**, iteration 6 — the
last rendering slice, and the one R3's rule binds: *any geometry that reads the data rather than
only the constants bumps the epoch*). R4 did not touch the matrix, which remains the complete
east-west record that the map's projection points at. Four honest limits are recorded in **§8**
rather than fixed here, two of them departures from this slice's own text.

## 14. R6 built as, 2026-09-15

*Iteration 6 of the ten-iteration Console run, and **the last rendering slice in this order**.
**Frontend-only** — everything below is under `o2/console/`; no Python was touched and the policed
counters do not move (pytest **3094 passed / 12 skipped** under `-m "not slow"`, op types **40**,
catalog rules **114**). Authority: **implementation (Claude Code)** for the three column rules,
`SITE_BUDGET = 6`, the refusal to rank by bytes, the arc-folding rule, the epoch bump and every limit
named; the run's scope and order are the user's. Full record: CHANGELOG 2026-09-15 "Console level-0
R6 BUILT".*

### 14.1 Three rules in one pure module — and the one clause deliberately not built

`src/layout/columns.ts` (**new**, pure — no React, no DOM, in the idiom `trunk.ts`/`infra.ts`/`arcs.ts`
established) holds the whole of F5's structural half as three rules that only make sense together:

- **RANKING** — `orderSegments`: placed-host count **desc**, then CIDR. `?order=cidr` keeps the
  address order.
- **BUDGET** — `budgetColumn` against **`SITE_BUDGET = 6`** (in `src/model/budgets.ts`, beside the
  other budgets rather than at its use site, because a budget restated at its use site is two
  budgets): the rest collapse behind one **`+N more`** tile, lifted per group by a **repeatable**
  `?expand=<site key>`.
- **WIDTH** — `siteColsFor(shown)`: **2 across for four or fewer, 3 otherwise**, so a small group stops
  reserving a width it cannot fill; `columnWidthFor` derives the column from it, and `layout/infra.ts`
  moves to per-column widths to match.

**Collapsing and expanding never change a column's WIDTH**, and that is a property rather than a
coincidence: a group only collapses when it holds more than `SITE_BUDGET` segments, and both
`SITE_BUDGET` and anything larger map to the same `SITE_COLS` — so the six shown tiles sit at exactly
the same coordinates before and after an expand, which is §3 R6's own locality clause, asserted rather
than commented (`siteColsForIsStableAcrossBudget()`).

`layout/level0.ts` publishes **`overflow: OverflowTile[]`** and **`hiddenSegments`**; the tile itself
now carries **`CIDR · hosts · bytes (north-south and east-west) · up to 3 role badges · evidence
ring`**, with `model/graph.ts`'s new **`GraphSegment.eastWest` DERIVED from `trafficPairs`, never
recounted** — so a tile's byte line and an arc's width cannot disagree about the same pair (LES-025,
the same rule R3's badges got from `roleHistogram`). `model/zoom.ts` gains `?order=hosts|cidr` and the
repeatable `?expand=`, both **view flags** in R1's taxonomy: they change *which elements are drawn*,
so `clearFilters` preserves them.

**Measured, on the two real bundles.** `corpapcn-twin`: **32 segments → 18 drawn tiles + 14
collapsed**, and **32 again** with every group expanded. The pit bundle's 19-segment `10.80` group
goes from **seven tile rows to two plus the overflow tile**, taking the plane's **height 1282 → 834**.

**The clause deliberately NOT built: a tile's SIZE does not vary** (an implementation call, open to
veto). §2 F5 lists "every tile the same size" among the symptoms and §12.6 named a varying size as
R6's, but §3 R6's own scope line does not — and a tile whose size followed its volume would move
every other tile in its column the moment a host appeared, which is precisely the column-preserving
locality `layout0` is built to hold. Magnitude is carried by the byte figure on the tile, by the trunk
leg's width and by the arc's.

### 14.2 The disclosure round — a projection must say what it hides on the axis it is NOT ranked on

**This is the slice's real lesson, and it came out of the review rather than the build.** The
ranking is by **host count**; the collapse therefore hides segments that may carry most of the group's
**traffic**, and the per-tile byte figure structurally *cannot* reveal it, because a collapsed segment
has no tile. Measured on the shipped bundles:

| group | collapsed | share of the group's volume it holds |
|---|---|---|
| pit `10.20` | 4 of 10 | **75.1 %** of its north-south bytes — including `10.20.195.0/24`, **1.77 GB from one host**, the bundle's largest egress talker |
| pit `10.80` | 13 of 19 | **65.5 %** of its north-south bytes |
| `corpapcn-twin` `10.20` | 4 of 10 | **99.9 %** of its east-west volume |

So the fix round added, from **one helper** each time (LES-307's rule, third instance — the hover text,
the accessible name and the tier's budget note cannot drift because they are the same sentence):

- **hidden volume** in the `+N more` tile's hover text, its `aria-label` and the budget note —
  *omitted entirely when the collapsed set carried nothing*, because "0 B and 0 B" is noise and its
  absence is not a claim (the count is still there);
- **`· N held for collapsed tiles`** beside the pin count, since a pin on a collapsed tile is kept but
  not drawn (§8) — a state an operator would otherwise have to infer from a box that isn't there;
- a **merged arc says its protocol mix is unavailable** — *"protocol mix is per link; this curve merges
  2, so the matrix has each one"* — rather than silently dropping the clause an ordinary arc carries.

**The ranking itself stays as specified.** Ranking by bytes was considered and refused: a level-0 tile
is mostly a statement about *population*, and a byte ranking would collapse exactly the segments that
hold the twin (a quiet 30-host workstation segment sorts below a one-host egress talker). `?order=cidr`
is the other real question — *"where is 10.20.195.0/24"* — and is offered beside it. **Choosing the
ranking key was the lesser question; disclosing what the key hides was the load-bearing one**
(LES-319).

### 14.3 Two defects measurement found — a band measured from the wrong bottom, and arcs that would have vanished

**(1) The east-west band's top was measured from the lowest TILE.** A column can end *below* its last
tile once the `+N more` box takes a grid slot, so the band reached **201.8 units against its 140-unit
bound** — a bound R4 had proven at 300 pairs and which R6 broke without touching `arcs.ts`'s clamp at
all. It is now measured from **`columnsBottom`**. Reverting turns **9 tests red across 6 files**.

**(2) A collapsed segment has no tile, so every arc anchored to one would have silently disappeared.**
Measured before the fix: **24 of 40 pairs surviving on the pit bundle and 16 of 31 on
`corpapcn-twin`** — and the *byte* loss is **twin-specific**, which is the part a single-bundle check
would have missed entirely: pit keeps **98.9 %** of its east-west volume (its dominant pair has both
ends drawn), while `corpapcn-twin` keeps **4.6 % and loses its widest arc outright**. `arcs.ts` now
carries **`ArcAnchor.key`** and draws **one curve per ANCHOR pair**: a collapsed segment's arcs fold
onto its group's `+N more` anchor and **merge**, carrying summed volume and a **`pairs`** count, by
exactly the argument R4 used for an observed-and-asserted pair (two identical curves on the same two
anchors are one curve plus a misleading count). A pair whose two ends fold to the **same** anchor is
not drawable — an arc from a box to itself is not a picture — and is counted in **`internalPairs`**
rather than dropped in silence; `foldedPairs` counts the rest. Reverting turns **6 tests red**.
Generalized as **LES-316**.

### 14.4 `PLANE_EPOCH` 2 → 3 — the case the rule was reserved for

R2 designed the stamp so a slice that moves a **pinnable** element cannot forget to invalidate a pin,
and R3 established the rule the hash cannot carry: *any geometry that reads the data rather than only
the constants bumps the epoch* (§12.3). **R6 is the case that rule was written for** — the budget
changes *which tiles exist* — so the epoch goes to **3**. `plane.ts` **also** hashes the three new
budget constants, so a later slice that merely retunes `SITE_BUDGET` or the column thresholds
invalidates pins without needing a second manual bump. Blast radius is unchanged and still zero: the
only `layout.json` in the tree holds `"pins": {}`, and a legacy or stale pin is accepted and flagged,
never dropped.

### 14.5 The goldens, accounted for — re-key by identity before believing a diff

**The raw diff reads 366 / 374 changed leaves and 326 removed** on the two level-0 goldens, which
reads as a rewrite of the layout. **Re-keyed by identity** — tiles by CIDR, arcs by pair, devices by
id — it is **69 changed and ZERO removed**: nothing was lost, and the raw count is the ranking
**permutation** re-indexing embedded `hosts[]` arrays. Three causes, in order of size:

| # | cause | evidence |
|---|---|---|
| a | the plane **narrowing** from `siteColsFor` (a ≤ 4-tile group is two across) — the largest contributor, and the one missing from the first account | `width` **2374 → 1514** and **2932 → 1900** |
| b | the band-top fix (§14.3) | `rails[4].box.y` **470 → 482**, exactly `SITE_PAD` |
| c | the permutation plus the new keys | `overflow` (a new top-level array), `hiddenSegments`, `foldedPairs`, `internalPairs`, `arcs[*].pairs`, `segments[*].eastWest` |

**Every box that moved moved in `x` only; `y` is invariant at 348.** Three level-1 goldens gained
**+2 leaves each** (`segment.eastWest` — `layout1` embeds `GraphSegment`, LES-300's third instance),
**both matrices are byte-identical**, and **two new goldens** — `level0-collapsed` and
`level0-collapsed-cidr` — document both orders, as §3 R6 requires. Generalized as **LES-320**: a
reordering slice reports a rewrite, and the attribution must enumerate *every* cause.

### 14.6 Gates, and the acceptance clauses

**Vitest 647 passed / 8 skipped** (from 594/8 at iteration 5); `npm run typecheck` clean from zero;
`npm run build` exit 0; `pytest -m "not slow"` **3094 passed / 12 skipped**, unchanged — no Python was
touched. `doc-stats.py --check` clean.

**§3 R6's acceptance holds, asserted as a RULE rather than as the literal figures** (the correction
blockquote under §3 R6): `shown === min(SITE_BUDGET, total)` and `hidden === total − shown`, verified
on **both** real bundles — pit `10.80` draws 6 and says `+13` (19 segments), `corpapcn-twin` `10.80`
draws 6 and says `+10` (16). Expanding via the URL yields the full set **with every previously-shown
tile at an unchanged position**; a collapsed segment stays reachable in the outline, in search, in the
matrix and by drill-down; and a pinned tile that is later collapsed keeps its pin and reappears at it
when expanded (§8). `noConnectorCrossesATile` holds **collapsed and expanded**, on every level-0
golden including the two new ones.

No live-verify — nothing here deploys or touches a cluster, and the standing `axrange serve` is still
deliberately **not** restarted (`docs/PRODUCT_GAPS.md` GAP-084), so the live Console still shows
pre-R1 code.

### 14.7 What this slice deliberately did not do — and what it hands forward

**A tile's size does not vary** (§14.1) and **the ranking is not by bytes** (§14.2) — both deliberate,
both open to veto. R6 did not touch the matrix, which remains the complete east-west record the map's
projection points at, nor level 1, which has its own budgets.

**What it hands forward is one named follow-up: COLUMN WRAPPING.** R6 bounded the plane's *height*;
the *width* is now structural (plane **2030 × 834**, **939 px off-screen** at a 1600 px window — §8),
and lowering the budget further would hide more of the twin to buy pixels the layout cannot give
back. Wrapping site columns onto a second row is its own slice, unwritten, and R1's off-screen readout
is the honest mitigation until it exists.

**With R6 every slice in this order is built** (R1–R7). Iteration 7 is
`docs/WORK_ORDER_console_canvas.md` §3 **S5.1.a** — the run's first editing slice, and the geometry it
drops into is now the one this order specified.
