*The Design area of the axRange Console (`docs/design/16-unified-console.md`): a single-page
application that displays a twin's discovered structure at three levels of abstraction, lets an
operator edit it as assertions, saves, applies (recompiles the cast) and refreshes. The authoring
surface `14-operator-assertions.md` §4 names. Everything below describes this area's function,
unchanged since it was built; its place in the app changed 2026-09-29 — see the note immediately
below.*

> **Superseded in part, 2026-09-29 (`docs/design/16-unified-console.md`; `docs/CHANGELOG.md`
> 2026-09-29).** What this file calls "the SPA" is now the **Design area** of one Console: its
> routes are re-rooted at `/design/...` (old `/imprints/...` paths in this file redirect there —
> §7's grammar table is otherwise unchanged), and it is reached from the Console's Range/Twin/
> Catalog areas rather than being the whole app. **"Starts clean, nothing ported from the Grafana
> plugin" and "independent of the plugin"** (§9, and CLAUDE.md) are superseded: the unified Console
> absorbs the plugin's deploy tree, guarded-deploy flow and "why empty" states into its Catalog and
> Twin areas, and the plugin itself is deprecated. **Kept, not reversed:** everything below about
> what this area does — the three zoom levels, the deterministic layout, the assertion-record
> edit model, save/apply/refresh, the etag discipline — is unchanged; only the surrounding app
> changed. The S7 Grafana iframe embed (§9's "built as" note) is retired along with the plugin: the
> Design area is reached inside the Console, not framed inside Grafana.

> **STATUS: DESIGN RATIFIED 2026-09-04 (Authority: user). BUILDING — §10's API additions are
> BUILT (S1, 2026-09-05/06), the SPA scaffold is BUILT (S2, 2026-09-08), the read-only drawing
> slice — levels 0–2 + the matrix over pure golden-tested layout modules — is BUILT (S3,
> 2026-09-09), and **pins are BUILT (S4, 2026-09-10)** — a drag repositions a segment tile or a
> whole site as an absolute override with no reflow, persisted to `<imprint>.layout.json` and reset
> to the pure function, and **editing and save are BUILT (S5, 2026-09-10)** — seven edit gestures,
> each producing exactly one `axr-assertions/1` record, with the three visible states and the
> change-list panel, and **apply and refresh are BUILT (S6, 2026-09-10)** — Apply saves any pending
> edits, recompiles over the real endpoint, and on a genuine change reloads the bundle and highlights
> what changed for five seconds; a byte-identical recompile and a changed-but-undiffable one are both
> rendered honestly rather than as an indistinguishable no-op. **S7 (the Grafana embed) is open** —
> `docs/WORK_ORDER_console_canvas.md` §12/§13/§14/§15/§16/§17. **Two post‑build items opened
> 2026‑09‑11 from a review of the live canvas:** the level‑0 rendering did not read as a network
> (no trunk, no rails, tie‑lines and lanes crossing tiles, illegible scale) —
> `docs/WORK_ORDER_console_level0_rendering.md` R1–R7, OPEN, whose §4.1 ruling replaces the
> orthogonal east‑west lanes below with volume‑weighted arcs; and the built S5's editing model is
> amended (that order's §3 S5.1: selection‑driven, clone‑and‑drag, host generation).** **S5.1.a —
> selection drives editing — is BUILT (2026‑09‑15, `docs/WORK_ORDER_console_canvas.md` §18):** the
> selected element carries its facts and actions, a segment is added from a `+` on its site, and the
> selection model, Escape order and `add: segment` semantics below (§7, §8) are as built; and **S5.1.b
> (clone and drag) + S5.1.c (generated hosts) are BUILT (2026‑09‑16, that order's §19)** — one gesture
> may now stage a **batch** of records all‑or‑nothing, a clone's drop position is an S4 pin, and a
> placed pending segment is drawn hatched on level 0 (§4, §8) — and **S5.1.d + S5.1.e are BUILT
> (2026‑09‑16, that order's §20), with which S5.1 is COMPLETE (a–e)**: pending work is drawn at both
> levels, counted **per gesture** with a whole‑gesture undo, and kept in a per‑imprint **draft** that
> survives a reload and every way out of an imprint (`docs/PRODUCT_GAPS.md` GAP‑094, closed) — and
> **S7, the Grafana embed, is BUILT (2026‑09‑16, that order's §21), executing decision D5**: the
> Grafana panel now **frames** this SPA at its own origin, the plugin's Track C imprints screen is
> deleted (GAP‑083 closed) and "deploy an imprint as a twin" survives there as a confirmed action.
> **Every slice of `docs/WORK_ORDER_console_canvas.md` is built.** The shape below was
> put to the user in a design session on 2026-09-04 after several single-diagram UI attempts had
> failed to scale, and its two forks were ruled the same day: **the SPA starts clean** (nothing is
> ported from the Grafana plugin) and **the layout is deterministic and prefix-grouped, with
> optional pins** (over a fully pinned layout). It is a **SPA, not a Grafana plugin**; a later
> Grafana integration must stay a small task (§9). A visual mockup of the three levels and the
> Console work order follow this note, the work order separately at the user's request. The
> assertions model, save/apply semantics, drift rule and first edit set are `14` §3–§4 and are not
> restated here; this note is the *surface*.

## 1. Why this exists, and why the last attempts failed

The plugin under `o2/grafana/plugins/axellio-axrange-control-panel/` draws one graph with React
Flow over `d3-force` and `dagre`. Every version of it failed the same two ways on a real twin (the
CorpAPCN export: 786 nodes, 190 local, 1437 edges, 32 segments): every element competed for one
plane, so the diagram became a hairball; and the layout was not a function of the data, so every
recompile reshuffled positions. The second failure is fatal for an edit → apply → refresh loop
specifically: after apply the operator has to find everything again.

The advice to make the diagram hierarchical and zoomable — "Google Maps for the network" — fixes
the first failure and nothing else. It leaves the layout unstable and the edges on the diagram,
which is what actually stops scaling. This design keeps the hierarchy and adds four things:
**representation swaps instead of pixel zoom, a deterministic identity-keyed layout, edges that
leave the diagram at scale, and a map that is one of several coordinated projections of the same
graph rather than the whole UI.**

## 2. Principles

1. **Zoom swaps representation; it never scales pixels.** An aggregate becomes its members; a
   member becomes a facts table. Each level has a bounded element count (§3).
2. **Layout is a pure function of identity.** Same imprint, same picture, on every machine, with
   nothing saved. A recompile that adds a thing moves nothing else.
3. **Edges leave the diagram.** Few, thick, aggregated bundles on the map; the complete set in a
   matrix.
4. **The map is one projection.** Outline, map, matrix and inspector show the same graph and stay
   in sync; the URL is the state.
5. **The canvas is an assertions editor, not a drawing tool.** Every gesture maps to one record
   kind of `14` §3; nothing else is editable.
6. **Evidence is a visual channel at every level.** The six classes, confidence, ghosts, overrides
   and drift render the same way everywhere, including the outline tree.

## 3. The three levels

| level | what it shows | elements on CorpAPCN | route |
|---|---|---|---|
| **0 — network** | a tiered path diagram: Internet, the WAN edge, the infrastructure devices, the segment groups by /16, the east-west band of conduits (§4). Each tile: CIDR, deployed / ghost host counts, occupancy, **up to three named role badges** (`DC 2`, `EP 14` — level-0 R3, 2026-09-14, replacing the unlabeled role histogram bar, which said *mixed* and nothing more at tile width; the full histogram stays in the outline and the inspector, and the tile discloses "3 of 5" on hover and in its accessible name), an evidence-mix ring, and **its north-south and east-west bytes in this window** (level-0 R6, 2026-09-15) | **18 drawn tiles of 32 segments** (R6: each site group draws its busiest six and collapses the rest behind one `+N more` tile), 6 devices, 22 asserted arcs | `/imprints/{id}` |
| **1 — segment** | the spine drill: the segment as a spine, its router leg on top, **role bands** of host chips off the left, conduit peers and the way off the segment off the right, a *below threshold* band of ghosts; role-to-role bundles on request (§4) | ≤ 6 bands × ≤ 15 chips | `/imprints/{id}/segments/{cidr}` |
| **2 — host** | the inspector as the main pane: keys and names, role with class / confidence / source, listen set with protocols, dial set, edges grouped by peer role and segment, persona, every field's evidence, assertions affecting it, actions | one facts table | `/imprints/{id}/segments/{cidr}/hosts/{key}` |
| **matrix** (any level) | the adjacency of the current scope: roles × roles at level 0, hosts × hosts inside a segment; a cell is flows / bytes / protocol; click a cell for the edge list | ≤ 40 × 40 | `?view=matrix` |

**As built (S2, §7):** the matrix is the **path** `/imprints/{id}/matrix`, with `?view=matrix`
accepted as an alias and normalized to it; a host also has the **flat** spelling
`/imprints/{id}/hosts/{key}` alongside the segment-nested one above; and a CIDR in a path is written
`10.80.20.0-24`.

Aggregates that exceed a budget collapse: a role band with more than 15 hosts shows one chip per
group with its `count:` and expands on click. The data makes budgets cheap — the cast's largest
group is 15 hosts and no segment carries more than 6 groups.

## 4. Layout: deterministic, prefix-grouped, with optional pins (ruled)

**Level 0 is a tiered path diagram — the idiom of the CorpAPCN topology report at
`10.80.72.52:8088` (ruled 2026-09-04, replacing a flat prefix grid).**

> **Amended 2026‑09‑11 (Authority: user; `docs/WORK_ORDER_console_level0_rendering.md` §4.1).**
> The east‑west band is drawn as **volume‑weighted arcs anchored at each site group's bottom
> edge**, not as orthogonal lanes from the tiles: a lane from a row‑1 tile drops through every row
> beneath it, one channel per conduit does not bound the band, and equal strokes hide magnitude.
> Observed segment‑pair traffic (aggregated from the export's edges) draws as arcs with stroke width
> `log(bytes)`, top‑K on the map and the full set in the matrix; **asserted conduits draw as
> distinct arcs in the edit colour** (dashed when no traffic was observed), which preserves the
> one‑arc‑one‑conduit identity for the asserted set. The same order adds the trunk (Internet → tap
> → WAN edge → one leg per group, width by north‑south bytes), the tier rails, infra devices placed
> over their group with orthogonal stubs, role badges and per‑group tile budgets.
> **BUILT 2026‑09‑15 (that order's R4, §13; CHANGELOG 2026‑09‑15), and the lane clauses in §4 and §5
> are amended in place rather than left to be read through this note** — `lanes.ts` is deleted. Two
> departures from the slice's implementation sketch are recorded in that order's §8: an arc **drops
> to the band top before it curves** (a cubic reaches only ¾ of the way toward its control points, so
> the literal shape sagged through tiles), and the control depth is anchored at `max(bandTop, both
> endpoints)` so a pin cannot bulge a curve back up through its own column. The **anchor this note
> rules — the group box's bottom edge — is unchanged.** Tier is the layout rule,
top to bottom, each tier labelled by a rail at the left edge: **Internet** (a dashed pill; the
sink hosts that stand in for it, marked `compiler`); the **WAN edge** (the twin's egress — the
gateway's WAN leg and its SNAT, with the firewall-policy gap named); the **infrastructure** tier
(the gateway as a router device, and the servers the twin depends on — the domain controller,
the resolvers, the monitoring collector — as server devices, each carrying its evidence class);
the **segment tier**, grouped by /16 in prefix order (the first two octets for RFC 1918 /24s,
the longest common prefix otherwise), segments as tiles inside each group — **ranked by placed-host
count desc then CIDR, at most six drawn, the rest collapsed behind one `+N more` tile whose hover
text and accessible name state how many segments and how much traffic are in there** *(level-0 R6,
2026-09-15: this clause originally read "in CIDR order inside each group", which survives as
`?order=cidr`; `?expand=<site key>` lifts the budget, and collapsing is a projection of the **map** —
a collapsed segment keeps its outline row, its matrix axis, its search hit and its drill-down)* — the
single-segment /16s sharing one group so six one-tile groups do not each cost a row; and an
**east-west band** beneath, where segment-pair links are drawn as **volume-weighted arcs anchored at
each site group's bottom edge** *(amended 2026‑09‑11 by the level‑0 order's §4.1, Authority: user, and
**built 2026‑09‑15**; this clause originally read "every conduit runs as an orthogonal lane between
the two tiles it joins", which is **superseded** — a lane from a tile's bottom edge drops through
every tile row beneath it, one channel per conduit does not bound the band's height, and equal
strokes hide magnitude)*. What the lane model is **not** allowed to lose, and does not: **each
asserted conduit is still drawn as its own arc**, in the edit colour and dashed when the wire carried
nothing, so one arc still means one declared conduit — and the **complete** set of links, asserted and
observed alike, is in the matrix (§5). The gateway's legs are drawn one per group; the infrastructure devices are tied
to the segment that holds them — **positionally, as built (level-0 R3, 2026-09-14): a device sits
over the centre of the site column holding its segment and reaches that column's header with a
short orthogonal stub, so "tied to" costs no line across the canvas; a device whose segment has no
tile stays centred, dashed and unstubbed, because there is no group to point at.** Layout is still
a pure function of the data: tier from the device's place on the path, group from the prefix, tile
from the CIDR. Tiles and routes carry the **observed** CIDR — the imprint's identity, which
the topology export and the cast join on by segment name; the deployed, relocated CIDR appears in
the inspector under its declared-transformation tag (`14` §2). Non-local and unplaced entities
have no tile: the Internet pill stands for the external peers, and a *referenced / unplaced* drawer
off the outline lists the rest (refined 2026-09-04 from the work order). A new segment slots into its site and nothing else moves
except to make room. The bucket rule is a heuristic and is stated as one: a site that mixes
prefixes renders split until pinned.

**Level 1 is the spine drill** (the same report's zone view): the segment is a spine in the
middle carrying its CIDR, counts, zone and role tallies; the router leg it hangs off sits on top;
its hosts hang off the left in **role bands in a fixed role order** — infrastructure first
(`domain-controller`, `dns-resolver`, `file-server`, `mail-server`, `web-tier`, `db-tier`,
`monitoring-collector`, `egress-proxy`, …), then `corp-workstation`, then `generic-endpoint`, then
`unroled-endpoint`, then the *below threshold* band — two chips per row, hosts sorted by address,
a spoke from each row to the spine; its conduit peers and the way off the segment hang off the
right, where the report puts peer zones. Hosts are **never pinnable**: the bands are the layout,
which is what makes level 1 stable and dense.

**Pins.** An operator may drag a segment box or a whole site elsewhere; the pin is saved to
`<imprint>.layout.json` beside the imprint (`{"schema": "axr-layout/1", "imprint": …, "pins":
{"site:10.80": {"x", "y"}, "segment:10.80.31.0/24": {"x", "y"}}, "plane": "l0-1-<hash>"}`), versioned
like the assertions file but **never evidence** and never read by the compiler. *(The optional
`plane` stamp was added 2026-09-14 by the level-0 R2 slice — `docs/WORK_ORDER_console_level0_
rendering.md` §11.4, LES-289: a pin is an **absolute** coordinate on a plane the document otherwise
does not identify, so a later slice that moves the plane's origin re-points every stored pin without
changing a byte of it. The stamp names the plane the pins were authored against; the API round-trips
it as an opaque token and the SPA compares it, accepting stale or unstamped pins **with a note**
rather than drawing them silently wrong or silently dropping them.)* A pinned element ignores its computed
slot; everything unpinned keeps its computed place; *reset pins* returns to the pure function. The
choice degrades gracefully: an operator who pins every segment has a fully pinned layout, one who
pins nothing still has a usable one — the reverse was not true, which is why the alternative lost.

**A segment the operator ADDS is drawn at its pin, and reflows nothing (S5.1.b, 2026-09-16).** The
pure layout lays out the **observed** topology, and an added segment takes no grid slot in it: it is
drawn only once it has been **placed** — a clone's drop position, committed as an ordinary
`segment:<cidr>` pin through the same writer a drag uses — at that absolute top-left, hatched while
*pending* and outlined once *saved* (`15` §8's three states). Two consequences follow from the pin
being absolute and are handled rather than hidden: a placed tile whose group the per-group budget has
collapsed is **held, said, and drawn when `?expand=` opens the group** (the same rule as a pin on a
collapsed tile), and a placement that overlaps a drawn tile **names what it covers**, because an
absolute pin may overlap by design but a tile that silently stops taking clicks underneath one is not
a design. A clone is *proposed* at the next grid slot of its source's column, clamped to that
column's own box — never dropped into the corridor the east-west arcs run through — and the operator
moves it from there.

**An UNPLACED added segment is drawn too (S5.1.d, 2026-09-16), and it covers nothing.** A segment added
from the site `+`, or generated with hosts, has no placement — so it is **appended to its own site
column** through the same slot rule the clone proposal uses, and, when that column has no room, parked
in a lane **clear of every box the plane draws**. That is the one difference between the two: a
*proposal* is a starting point the operator is about to move, so it may overlap and says what it
covers; an *appended* tile is simply how that segment is drawn until they move it, so it must land on
nothing. Both obey the same rule — *you do not cover the box you are inside* — and both are computed
from one set of drawn boxes, so the lane and the coverage report cannot disagree. A segment whose group
the budget collapsed is **held and disclosed** exactly as a placed one is, and one whose group the map
draws no column for at all is **reported** rather than silently dropped. **Level 1 draws pending work
as well:** a pending segment is a hatched spine with its staged hosts as hatched chips, and a host
added to an *observed* segment is a hatched chip beside that segment's observed bands.

Layout is computed in pure TypeScript modules with golden tests: the same export yields the same
coordinates, byte for byte, and adding one segment changes only that segment's coordinates.

## 5. Edges

Level 0 draws the east-west band as **volume-weighted arcs**, each anchored at its site group's
**bottom edge** — never inside a tile *(amended 2026‑09‑11 by the level‑0 order's §4.1, Authority:
user; **built 2026‑09‑15**, that order's §13. This paragraph originally read "conduits as orthogonal
lanes … and, on request, cross-segment traffic bundles in the same band, one lane per segment pair,
width by flow count, off by default above 40 pairs" — **superseded**: there is one band, not a band
plus an optional second drawing, and it is on by default)*. Two families share it. **Observed**
segment-pair traffic, aggregated from the export's own edges, draws with stroke width
`log(bytes)` — **top‑K on the map**, the complete set in the matrix. **Asserted conduits** draw as
distinct arcs in the **edit color**, **dashed when no traffic was observed on the pair**, so a
declared-but-silent conduit is visibly different from an observed-but-unasserted flow; a pair that is
both is drawn **once, as asserted**, carrying its observed volume. Links carry no
protocol color: color on the Console belongs to evidence class; protocol lives on hover and in the
matrix. Level 1 draws
**role-to-role bundles** inside the segment on request. A **selected host's own edges** appear
only while it is selected, as focus-plus-context. The complete edge set lives in the **matrix**
view (§3): rows and columns in the same fixed order as the map, so the two agree; cells colored by
flow count with protocol on hover; a click opens the edge list in the inspector. 1437 edges are a
colored grid, never 1437 lines.

## 6. Evidence encoding

Fill color is the **evidence class** — the six of `14` §2, one legend, chosen by a `dataviz`
pass so light and dark themes both hold. Saturation is **confidence**. A **dashed outline** is a
ghost: below the deploy threshold (`13` §3.4), dropped by the cast, suppressed by an assertion, or
referenced-but-unseen. A **badge** marks an assertion overriding another class, with the
overridden value in the inspector; a second badge marks **assertion drift** (`14` §4, flag-only).
The outline tree carries the same encoding as small dots beside each node, so a filter such as
"everything imputed" reads in the tree without opening the map.

## 7. Panes, navigation, the URL

Three panes: **outline** (left — sites → segments → role groups → hosts, with counts and evidence
dots), **map** (center — the current level, or the matrix), **inspector** (right — facts, edits,
edge list). Selecting in any pane updates the others. The route is the zoom (§3's table);
`?view=matrix`, `?ghosts=1`, `?class=imputed`, `?occupancy_lt=0.8` are filters (a CIDR in the route
is spelled `10.80.20.0-24`, since a slash cannot travel in a path segment); the breadcrumb is the
route; Escape goes up a level; the back button works. **Search is the primary navigation** on a
large twin: a name, an address, a role or a CIDR jumps to the element at its level.

**The grammar as built (S2, 2026-09-08 — `docs/WORK_ORDER_console_canvas.md` §13.1).** It is
defined **once**, in `o2/console/src/model/zoom.ts`, as a pure round-trippable function behind a
single catch-all route — a second copy in `routes.tsx` would be a second grammar.

| route | level |
|---|---|
| `/imprints/<id>` | 0 — the network |
| `/imprints/<id>/segments/<cidr>` | 1 — the segment |
| `/imprints/<id>/hosts/<key>` | 2 — the host, flat |
| `/imprints/<id>/segments/<cidr>/hosts/<key>` | 2 — the host in its segment context (§3's spelling) |
| `/imprints/<id>/matrix` | the adjacency of the current scope |

Two mechanical spellings, because §3/§7's notation is not directly expressible in a URL: **a CIDR's
prefix length is written with a hyphen** — `/segments/10.80.20.0-24`, never `%2F`, which some
proxies normalize or reject, and these URLs are meant to be typed and pasted — and **`?occupancy<0.8`
is spelled `?occupancy_lt=0.8`**. `?view=matrix` is accepted as an **alias** and normalized to the
`/matrix` path, so the filter form above and the route form are one thing. Filters are `?ghosts=1`,
`?class=<one of the six>`, `?occupancy_lt=<0..1>`.

**A value that cannot be used is reported, never silently dropped** — an unparseable
`occupancy_lt`, an unknown `class`, a duplicated query key and a `?view=` at the wrong level all
surface in the parse result's `problems`, and a malformed `%` escape is an **error**, not a quiet
fall-back to the catalog root. Because the state is the URL and nothing is hidden in a store, the
breadcrumb, Escape-goes-up and the back button are consequences rather than features — and a deep
link survives a reload only because `axrange serve --spa-dir` serves a history fallback (§9).

**Selection (S5.1.a, built 2026-09-15 — `docs/WORK_ORDER_console_canvas.md` §18).** What is selected
decides what the inspector offers (§8), and it has **two sources**:

- **The URL's subject.** A host route selects its host; a segment route, or a segment-scoped matrix,
  selects its segment. Opening an element — from a tile, a chip, the outline or search — therefore
  *is* selecting it, which is how a segment the per-group budget collapsed (§3) reaches the same
  actions with no tile. A chip, an infrastructure device and a search hit always **drill**: a host is
  never an in-place selection.
- **An explicit in-place selection** — a level-0 tile's select control (clicking the tile itself still
  drills), the level-1 spine, a segment this file adds (listed in the outline; the map does not draw it
  yet), or a site `+`'s "add a segment here". It wins over the URL's subject, and it lives in the same
  one slot as a matrix cell or an O7 drawer, so there is one selection at a time.

**An explicit selection lives exactly as long as the pathname.** No query flag moves the page to
another place — a view flag re-budgets the map, a dimming filter emphasizes a subset, `?changed=1`
marks what an apply moved — so none of them resets the selection or what was typed under it. Each
fetched value is bound to the subject it was read for, so switching imprints can never show — or let a
gesture stage — one imprint's elements under another's id. A new select gesture moves keyboard focus
into the actions **once per gesture**, never per re-render or remount, and the selection is marked
`aria-current` on one element per view.

**Escape, in order:** (1) an explicit selection that differs from the URL's own subject is cleared,
and focus returns to the control that opened it — or to the map pane when that control is gone, never
`body`; (2) with focus inside the actions, Escape steps out to the map; (3) otherwise it goes up a
level — **except** that leaving an imprint (the network level's parent is the catalog) with **unsaved
edits** is refused: Escape stays and announces *"N unsaved edits — save or discard them before
leaving"* in a live region. Escape is never the gesture that discards work, and an Escape pressed inside
a text field is left to the field. (The breadcrumb, the
outline's imprint links, the back button and a reload still leave without asking, because pending
edits have no draft yet — S5.1.d, `docs/PRODUCT_GAPS.md` GAP-094.)

## 8. Editing, save, apply, refresh

Every gesture is one record kind (`14` §3, first edit set as ruled):

| gesture | record |
|---|---|
| level 0: "add segment" form (CIDR, name, zone type) | `add: segment` |
| level 0 or 1: "add host" form on a segment (key, role, listen set) | `add: host` |
| level 1: drag a chip to another band | `override: role` |
| any: select → "suppress" | `suppress` |
| inspector: image, sizing | `override: image` / `override: resources` |
| level 0: drag from one box to another, or a form | `add: conduit` |
| inspector: remove an assertion | the record is deleted from the file |
| level 0: **Alt‑drag a tile** (or inspector *Clone this segment…* + placement) → clone form *(added 2026‑09‑11)* | `add: segment` + N × `add: host` (+ M × `add: conduit` if mirrored) — one gesture, one batch |
| level 0: site header `+` → add‑segment form with an optional hosts block *(added 2026‑09‑11)* | `add: segment` + N × `add: host` |

**Amended 2026‑09‑11 (Authority: user; `docs/WORK_ORDER_console_canvas.md` §3 S5.1, a post‑build
amendment to the S5 of 2026‑09‑10).** Editing becomes **selection‑driven**: the selected tile, spine
or chip carries its actions in the inspector, and the built `EditPanel`'s context‑free forms are
removed (their record layer stays). §2's principle 5 is refined, not broken: **a gesture
may emit a batch of records** of the ruled kinds (a clone is one `add: segment` plus its hosts and,
optionally, its conduits), never a new kind, and the change list shows the gesture with its records
beneath it. Cloned records are `asserted` with the operator's note; the source's evidence never
travels with the copy.

**The batch as built (S5.1.b–c, 2026-09-16).** A batch is **all or nothing**: every record is checked
— against the writer's closed sets, against the store's identity rule, and against `14` §3's
cross-record refusals (including the batch's own earlier records) — before any of it is staged, and
the first refusal refuses all of them, naming the record it stopped at. **A batch never replaces an
existing record**, where staging one deliberately does: sixteen records that silently overwrite a
saved host are not a gesture anyone meant. Each entry carries the gesture it came from — a browser-only
tag the file has no concept of, which survives a save and re-read only while the record on file is
still, by value, the record the gesture staged. A clone's **placement is a pin, not a record** (§4);
its note is written on every record as `cloned from <source cidr>: <note>`; and what cannot travel is
**listed, never dropped** — hostname- and MAC-keyed hosts (a hostname names one machine), a conduit
whose other end is a network the map has no CIDR for, and a conduit to the clone's own network. A CIDR
that **exactly** matches a segment already in play — observed, cast-declared, or added by this file —
is refused, because a clone creates a new segment and reaching an existing one is *Add host here*; an
overlap only warns (the site `+`'s rule, and the asymmetry is deliberate: an *add* of an observed
segment confirms it, a clone of it would not).

**Where a generated host's listen set comes from, and what it cannot know (S5.1.c, 2026-09-16).** The
add-segment form can generate N hosts on a fresh segment, addresses allocated from `.10` upward
skipping every address the export carries and every key the file already holds. Their `listen` set is
prefilled from **the persona library** — the one place this repo records which ports a role is reached
on — through a table generated from `axrange/personas/catalog/*.yaml` and drift-tested, never
hand-written. The derivation is **drive-aware**: a `real-service` persona *serves* its emits (its own
id is the listening role) while every other drive *dials* (the emit's `peer_role` listens). Its one
blind spot is disclosed rather than guessed at: a `real-service` persona's emits are not split into
what its daemon answers and what its host calls out to, so a port the role **dials** can appear in its
own entry (a web tier's 3306 to the db tier). The form says so where it offers the default, names the
roles it is true of, and treats the prefill as a starting point to prune — never as a claim about the
host. The honest fix is an emit **direction** in the persona catalog, which is a version-locked
decision of its own (`docs/PRODUCT_GAPS.md` GAP-095).

**Gestures hang off the selected element (S5.1.a, built 2026-09-15).** The table's forms are reached
from what is selected (§7), not from the zoom level: a selected **segment** offers *Add host here*,
*Clone this segment…* (shown unavailable, with its reason, until S5.1.b), *Add conduit to…* and
*Suppress hosts…*; a selected **host** offers *Change role*, *Override image / resources* and
*Suppress*. **"Add segment" is a `+` on the site it will be born in** — each map site header and
outline site row carries one, and the outline root's `+` is the site-less add, which lands in `misc`.
The form prefills the **next free `/24` in the site's two-octet prefix** (free of every CIDR in play,
at any prefix length — never an observed CIDR), the conventional name (`seg-` plus the dashed network
address, disambiguated only on a clash; a name another network holds is refused, STR-095) and a zone
type taken from the site's dominant declared zone, then the cast's, then `simple` — the twin-config
default, since one `vlan` segment makes the whole twin a VLAN zone. The CIDR field follows the
suggestion until it is typed in, and the live note and the Stage refusal read one canonical value.
Nothing about the record layer changes: every gesture still emits exactly one record through the same
builder, and the change list, save and apply below are untouched.

**What an `add: segment` means depends on whether its CIDR is observed.** For an **unobserved** CIDR
the compiler synthesizes the segment, and it survives with no host. For an **observed** CIDR the add
**confirms rather than creates**: its `name` and `zone_type` apply to the observed segment, and the
compiler keeps that segment only when a host is placed on it (aximprint `synth/compiler.py`, the
asserted-segment pass; `14` §3's dated correction). The canvas therefore **refuses** an add of a
segment the cast declares — joined by **name** through the cast's segment roster (§10), with renames
this file records mapped through — pointing at the gesture that is wanted, *select it and use Add host
here*; **refuses** a CIDR a pending or saved add already holds; **allows** an observed CIDR the cast
does not declare under that name, saying it is *renamed or pruned* (the cast joins by name) and that a
host is what keeps it; and only **warns** on an overlap, which axRange's validator refuses (NET-010)
unless the cast is relocated. A segment the file adds is selectable before the map draws it: it takes
hosts and conduits, and removing it when it is the **only home** of records staged on it lists them and
says what each kind would meet without it — a host fails the apply, a conduit is reported ineffective.

Edits carry **three visible states**: *pending* (in the browser only, hatched), *saved* (in the
YAML, not yet compiled, outlined), *applied* (in the cast, rendered as `asserted`-class elements).
A **change-list panel** shows the diff between the current cast and the assertions file, grouped
by record, each marked with the kind of change it will be for a standing twin — an add or remove
flows through `te deploy --update`, a mutation of an existing node is a fresh deploy (`14` §4).

**Grouped by GESTURE, as built (S5.1.d, 2026-09-16).** The list shows **one entry per gesture** —
`clone of 10.80.20.0/24 → 10.80.31.0/24 · 1 segment · 17 hosts · 2 conduits · pending` — expanding to
its records, which remain individually removable, and **undo removes exactly that batch** (dropping the
pin of a segment it takes out of play, re-saving once where saved records were involved, asking first
where the undo would strand a record outside the gesture, and leaving the document byte-identical to
before it). Two rules keep it honest: a gesture that staged **one** record is one plain row, not a
group with ceremony; and a group's state and deploy tag are the **weaker** of what it holds, so a
half-re-staged clone never reads "saved" and a batch containing any `override` never reads as an
`--update`.

**Pending work survives the browser: the DRAFT (S5.1.d, 2026-09-16 — `docs/PRODUCT_GAPS.md` GAP-094,
closed).** Pending records are kept per imprint on the operator's browser, so a reload, an imprint
switch, the breadcrumb, the outline's imprint list and the back button no longer discard them; a save
clears the draft. **Restoring is reconciliation, never a replay, and never a merge** (the rule that
makes a stale etag a refusal, not a silent write, holds here too): a record the file now holds
**identically** is dropped as redundant; one the file holds **differently** under the same key says so;
the restore runs the **same cross-record refusals** a gesture runs, since a draft is the only way a
record reaches the store without passing through one; and what has moved underneath — the assertions
file, or the bundle's compiler build — is **reported beside** the restored set rather than quietly
accepted. Where the bundle's build could not be read at all, the restore says the check **could not be
made**, which is a third outcome and not silence. A refusal that the **world** caused keeps the draft
so the operator can resolve it; one that can never change drops it; and the offer to **discard** stands
wherever a notice is shown. A browser that blocks or fills its storage is told about **once**, by name,
and nothing is taken down. The draft is operator content, never a credential: the API write token stays
in memory for the life of the tab and is never persisted.

**The guard model.** Every in-app navigation passes through one function, and the guard sits **there**
rather than on each link, so a new link cannot forget it; it fires only when a navigation **leaves** the
imprint, and a prompt whose reason disappears **dismisses itself** rather than acting. Escape keeps its
own, stricter refusal with no one-click exit — a keypress is cheap to make by accident. The browser's
own Back is **deliberately not intercepted**: nothing is lost, and the substitutes are honest — the
guard says the work is kept as a draft, a **destination notice** names the imprint left behind with its
count and the way back, and the catalog says how many imprints on this browser hold unsaved drafts, for
an operator arriving cold.

**Save** writes the file and nothing else (`PUT …/assertions`). **Apply** saves, recompiles and
refreshes (`POST …/apply`): the SPA receives the new content fingerprint as `fingerprint_after`
(plus `pit_code_fingerprint_*` / `cast_code_fingerprint_*` and a `compiler_changed`/`compiler_note`
verdict for a compiler upgrade that rode along — GAP-087/GAP-088, CHANGELOG 2026-09-19), reloads
the bundle, and
highlights for a few seconds what changed against the previous fingerprint (`GET …/diff`). The
file carries an etag: a save against a file that changed since it was loaded is refused with a
reload prompt, never merged silently.

**The refusal as built (S2, 2026-09-08).** A stale etag is **412**, and the client's rule is
*report, never adopt, reload before retry*: the refused save's **pending edit stays intact**, the
server's etag is recorded in the conflict as **evidence of what the other writer wrote**, and the
store sets `mustReload` so no save can be re-issued until a real re-read has happened. Adopting the
etag a refusal hands back is the one tempting move that **inverts the guard** — the retry then
succeeds and silently overwrites the other writer, with every concurrency test still green
(`docs/LESSONS.md` LES-251). **409** is the different answer: an apply holds the imprint's lock, so
the recovery is to wait rather than to reload.

**Applied is joined back by the authored key.** A twin-side host removal is reported under the
twin's own node id, so the canvas learns which *record* caused it from
`GET …/diff`'s `suppressed_keys` **set delta** and, on a plain load, from `GET …/provenance`'s
`population.suppressed[]` — where `matched: false` distinguishes a record that matched **nothing**
from a real suppression, so an ineffective edit is never shown as applied. A per-removal
`authored_key` column was refused: the producer's accounting does not carry that correspondence
(`docs/WORK_ORDER_console_canvas.md` §13.2).

## 9. Architecture

- **A clean-start SPA under `o2/console/`** (ruled): React + TypeScript, Vite, plain **SVG** for
  the map — the per-level element budgets (§3) make canvas or WebGL unnecessary — with layout in
  pure modules and no `react-flow`, `d3-force` or `dagre`. The plugin's inspector and deploy tree
  are **not ported**; the plugin remains a read-only viewer until it embeds this SPA. *(Satisfied
  2026-09-16 by S7 — it embeds it now; see the built-as paragraph below.)*
- **Served by `axrange serve`**, which already mounts a built SPA at `/` when `AXRANGE_SPA_DIR`
  points at one (`axrange/api/app.py`, `_maybe_mount_spa`); the dev server proxies `/api`. The
  API base URL is the SPA's only configuration.
- **Grafana later** is an iframe panel or a thin plugin mounting the same React root — a small
  task because the SPA touches no Grafana API and authenticates with the API's existing bearer
  write token (`_require_write_token`) for save and apply.

**The embed as built (S7, 2026-09-16 — `docs/WORK_ORDER_console_canvas.md` §21).** It is **an iframe at
the Console's own origin**, and of the two shapes offered above only that one works: the SPA calls
**same-origin `/api`**, so mounting its React root inside the plugin would put every read on *Grafana's*
origin, where the axRange API implements **no CORS** — every fetch would fail at the browser. Framed,
the SPA keeps its origin, its same-origin `/api` and its own bearer token, and the plugin proxies
nothing. The panel is configured with a **base origin** (plus an optional imprint id for a deep link)
and builds exactly `<base>` and `<base>/imprints/<id>`: **the URL grammar of §7 has one home**
(`o2/console/src/model/zoom.ts`), and anything deeper is reached by navigating inside the frame. Two
consequences are handled rather than hidden. A **cross-origin frame cannot report its own failure** —
`onLoad` fires for a blocked frame as readily as for a rendered one — so the panel claims no load
state, always offers an open-in-a-new-tab escape, and names what to check in likelihood order: whether
the **browser** can reach the Console origin (this frame is the one call the browser makes; everything
else in the plugin goes server-side through the datasource proxy), **mixed content**, Grafana's
`content_security_policy` `frame-src`, and last the Console origin's own frame headers. And a base URL
that cannot work — relative, non-http(s), carrying a query, a fragment or credentials — is **refused
with its reason** rather than framed anyway, because inside an iframe the failure would otherwise be
silent.

**What the plugin keeps, and what it gave up (decision D5, Authority: user, 2026-09-11; executed at
S7).** The plugin's Track C **imprints screen is deleted** — it rendered a tolerant API's placeholder
plus qualifier as a confident measurement (`docs/PRODUCT_GAPS.md` GAP-083) — and **the Console is the
one surface that renders a belief store**. What survives is the single action an operator wants from
inside Grafana: **"deploy an imprint as a twin"**, as pick → preview (the open, pure plan route) →
an in-panel confirmation naming the destination cluster's API URL → deploy, rendering **no** belief-store
measurement at any point.
- **State** is the URL plus a small store; data arrives through typed hooks over the DTOs below.

**Built as (S2, 2026-09-08 — `o2/console/README.md` is the operator-facing version of this).**

- **The stack, pinned:** React 18.3.1 + `react-router` 7.18.3 on Vite 8.2.2, TypeScript 5.9.2
  strict, Vitest 5.0.0 + jsdom; `node >=22.12`. `npm run typecheck` is clean **from zero** — the
  Grafana plugin's tolerated ~118-error baseline (LES-042) is not inherited into a new package.
  Layers: `model/` and `edit/` know nothing about React or the API, `api/` knows nothing about
  layout, `views/` compose them.
- **The SPA's only configuration is the API base URL, and in the normal case it has no value** —
  the app calls same-origin `/api/…`, which works both co-served
  (`axrange serve --spa-dir o2/console/dist`) and under `npm run dev`, whose proxy forwards it. Two
  variables exist and are **not interchangeable**: `AXRANGE_DEV_API` is read by `vite.config.ts` at
  config time and sets the **dev proxy's target** (never reaching the browser), while
  `VITE_AXRANGE_API` is **compiled into the bundle** as an absolute base URL that bypasses the proxy
  and needs CORS the API does not implement. They were one variable at first, which was a bug: Vite's
  `loadEnv` lifts every `VITE_`-prefixed variable into the client (LES-252).
- **The write token is supplied at runtime and held in memory for the life of the tab** — never
  compiled in, never in `localStorage`/`sessionStorage`/a cookie; a reload drops it. Reads carry no
  token, so a deployment fronting the API with a same-origin session supplies nothing and writes
  still work.
- **`axrange serve --spa-dir` serves a history fallback** (`axrange/api/app.py`), which §7's
  "the URL is the zoom" requires: `StaticFiles(html=True)` alone 404s every deep route on reload.
  `/api/*` (matched on the **decoded** path) and asset-suffix misses stay 404s rather than being
  answered with HTML and a 200; write methods are 405; the index is `Cache-Control: no-cache` and
  `/assets/` is `public, max-age=31536000, immutable`, which is the two-line policy a hashed-asset
  build needs (LES-256). Headless — no `--spa-dir` — is byte-identical to before.

## 10. API additions (the user's "you may have to update the api for UI")

All under `axrange/api/`, clean DTOs only (`docs/design/11-api-ui.md`), the imprint catalog root
pointed at the real cast bundles with the existing `AXRANGE_IMPRINTS_DIR` (default: the Track-C
fixtures) — the fixture stays the unit-test seam, the cast directory the live one.

| endpoint | purpose |
|---|---|
| `GET /api/imprints/{id}/topology` | the topology export (nodes with observation blocks and evidence classes, segments, edges, population) as a `TopologyExportDTO` — an **alias** of the existing `GET /api/scenarios/{id}/topology-export?source=imprint`, which already serves this DTO from the same reader; no second reader (2026-09-04) |
| `GET /api/imprints/{id}/cast` | the compiled bundle summary: twin config, params incl. `DEPLOY_MIN_OCCUPANCY`, `code_fingerprint` (the PIT row's build stamp, not a change detector), `bundle_fingerprint` (the bundle's CONTENT fingerprint — the same value an apply publishes as `fingerprint_before/after`, GAP-088; the Console stamps its drafts with this one), sidecar summary — and three additive rosters beside their counts, each in config order and **absent** (not `[]`) from a server that predates it: `conduit_roster: [{from, to}]` and `routers: [{name, segments}]` (level-0 R5, 2026-09-11), and `segment_roster: [{name, cidr, zone_type}]` (S5.1.a, 2026-09-15) — `cidr` the **deployed** network (relocated on a `--relocate` cast, so it joins the export by name) and `zone_type` `null` where the config declares none |
| `GET /api/imprints/{id}/provenance` | existing; gains the six classes and the real sidecar shape (the sidecar ruling of 2026-09-04) |
| `GET` / `PUT /api/imprints/{id}/assertions` | the assertions file as JSON, with a content-hash etag; write-token gated; a stale or missing etag is **412** naming the on-disk value |
| `POST /api/imprints/{id}/apply` | save + recompile — **synchronous**, behind a **per-imprint lock** (a concurrent apply is **409**); runs **both** producer stages and returns the new fingerprint; write-token gated |
| `GET` / `PUT /api/imprints/{id}/layout` | the pins file, same etag discipline; keys are `site:`/`segment:` only |
| `GET /api/imprints/{id}/diff?since=<fingerprint>` | what changed in the bundle since a fingerprint, for the post-apply highlight; an unknown fingerprint is **404** naming the known set |

The existing `_resolve_read_ir_path` fallback (LES-196) already lets the topology and node-detail
endpoints see a non-catalog imprint id; the new endpoints follow it.

**Built 2026-09-05/06** (`docs/WORK_ORDER_console_canvas.md` §12, CHANGELOG 2026-09-06 "Console S1
BUILT"). Five mechanics are settled and are what a consumer codes against:

- **The etag is a content hash and may arrive in `If-Match` or in the body** — disagreeing values
  are refused, so a header-stripping proxy cannot turn a guarded write into an unguarded one.
  **412** is the stale-etag answer; **409** means an apply holds the imprint's lock, a different
  situation with a different recovery.
- **`POST …/apply` is synchronous**, not `apply_id` + poll: a real apply measures ~2.7 s, and the
  endpoint runs as a sync handler off the event loop behind a process-local per-imprint lock. A
  reverse proxy in front of it needs `proxy_read_timeout` ≥ the backend's 120 s cap. The contract
  is written so a helper process or the aximprint watcher can replace the backend unseen.
- **The recompile is TWO producer stages** — `aximprint cast …` into a scratch directory, then
  `aximprint install-cast <scratch> <catalog-root> <id>` — because a cast alone writes generic file
  stems and only the install stage names them `<id>.*`. The cast's flags are read from the bundle's
  own **`cast_invocation`** block (`bundle_schema: axr-topology/1.5`); a bundle without one is a
  **422 naming the block**, never a guessed invocation.
- **Change is detected by a content fingerprint axRange computes** (sha256 over `<id>.yaml`,
  `<id>.topology.json`, `<id>.provenance.json`), not by the producer's `code_fingerprint`, which is
  a *build* hash and is identical across two different casts of the same code. The same fingerprint
  keys the `.history/<fp>.json` snapshots the diff reads.
- **The assertions vocabulary is a VENDORED artifact.** `axrange/api/schemas/axr-assertions-1.schema.json`
  is a byte-identical copy of aximprint's published schema; the writer derives its closed sets from
  it at import time and a drift test compares both the sets and the bytes against the sibling
  checkout. aximprint's compiler is the reader of record — axRange accepts and refuses exactly what
  it does (`14` §3).

`GET /api/imprints` is **tolerant**: it admits sidecar-less *and* canonical-sidecar bundles and
publishes `skipped: [{id, reason}]`, so pointing `AXRANGE_IMPRINTS_DIR` at `twins/` (was
`twin-configs-cast/`) lists the real bundles instead of returning an empty catalog -- every twin
folder, hand-authored ones included (they list sidecar-less).

## 11. Non-goals and limits

Not an editor of the deployed twin: the canvas edits the imprint, and the twin follows by
`--update` or a fresh deploy. Not a free-form drawing tool: only §8's records exist. No Grafana in
v1. No multi-user real-time editing: the etag is the whole concurrency story. No physical or L2
layout. Hosts are never pinned. Budgets are stated, not hoped for: about 40 boxes at level 0, about
100 chips at level 1, a 40 × 40 matrix — beyond them, aggregates collapse rather than the frame
rate.

## 12. Decisions

**Ruled 2026-09-04 (Authority: user):** a SPA, not Grafana; starts clean; deterministic
prefix-grouped layout with optional pins.

**Open, each with its v1 default:** the site bucket rule (v1: two octets for RFC 1918 /24s,
longest common prefix otherwise); the six-class palette (v1: a `dataviz` pass before the first
screen); the matrix in v1 (v1: yes, level 0 only); partial apply from the change list (v1: no —
apply applies the whole file); the highlight duration after apply (v1: five seconds, then a
"changes" filter keeps it reachable).

**Status of the palette item after S2:** `o2/console/src/theme/tokens.css` ships the six classes in
precedence order plus the pending/saved states, in light and dark, each class carrying a **fill
style** as well as a hue so `observed` and `referenced` stay distinguishable in monochrome and for a
colour-blind operator. That is the `dataviz` pass's **starting point, not its verdict** — the hues
stay open until S3's first screen.

**Ruled 2026‑09‑11 (Authority: user):** east‑west links are arcs, not orthogonal lanes; all seven
rendering slices of `docs/WORK_ORDER_console_level0_rendering.md` are commissioned (after S6, beside S7).
**Status after R6 (2026‑09‑15): ALL SEVEN are BUILT** — R5 + R7 (backend, 2026‑09‑11), R1
(width and rails, 2026‑09‑12), R2 (the trunk, 2026‑09‑14), R3 (the infrastructure band and role
badges, 2026‑09‑14), **R4 (the arcs themselves, 2026‑09‑15 — §4 and §5 above are amended to the
arcs model in that commit, as the ruling required)** and **R6 (tile content and per‑group budgets,
2026‑09‑15 — §3's level‑0 row and §4's segment tier are amended to the ranking, the six‑tile budget
and the tile's byte figure)**. One follow‑up is named rather than implied: the map still does not fit
at a 1600 px window (the plane is 2030 × 834 on the pit bundle), and **column wrapping** — site
columns onto a second row — is its own unwritten slice. The arcs shipped with two recorded departures from the slice's implementation
sketch, neither touching the anchor this note rules: a curve **drops into the band before it curves**
(a cubic reaches only ¾ of the way toward its control points), and its depth is anchored at
`max(bandTop, both ends)` so a pin cannot bulge it back up through its own column.

**Status after S3 (2026-09-09, `docs/WORK_ORDER_console_canvas.md` §14):** all seven open items ship
with the read-only drawing slice at their v1 defaults. Two resolved a clause the ruling had left
conditional — **O3** shipped the hosts × hosts matrix inside a segment as well as roles × roles (it
"cost nothing extra"), and **O6**'s level-1 rung-collapse and **O7**'s unplaced/non-local counting
landed as ruled (both "found in the data"). **O2**'s palette shipped as swappable `tokens.css` tokens
(still the starting point, not the verdict); **O5**'s post-apply highlight is a seam awaiting the S6
apply. One item outside this list is carried as a follow-up rather than closed: the east-west conduit
lanes (§5's edge bundles) are computed and golden-proven but not yet drawn in the live app, because
the `CastSummary` API carries only a conduit count — `docs/PRODUCT_GAPS.md` GAP-079.

## 13. What changes in the work

The Console work order (separate, per the user) covers the SPA in slices — read-only levels 0–2
against the real cast, then the matrix, then editing and save, then apply and refresh — and the
API additions of §10, which land in `axrange/api/` ahead of the SPA slices that need them. The
plugin's Track C imprints screen is untouched. Sequenced alongside reference ingest (`13` §7), after
the first golden; the read-only slices can start any time, since they need only the topology and
cast endpoints.

**Progress:** §10's API additions are **BUILT** (S1, 2026-09-05/06), the **SPA scaffold** is
**BUILT** (S2, 2026-09-08), levels 0–2 and the matrix are **BUILT** (S3, 2026-09-09), and **pins
are BUILT** (S4, 2026-09-10) — a segment tile or a whole site can be dragged as an absolute override
with no reflow, persisted to `<imprint>.layout.json` via `PUT …/layout` and reset to the pure
function, and **editing and save are BUILT** (S5, 2026-09-10) — seven edit gestures, each producing
exactly one `axr-assertions/1` record, with the three visible states and the change-list panel. And
**apply and refresh are BUILT** (S6, 2026-09-10) — Apply saves any pending edits first, recompiles
over the real endpoint (no longer gated on aximprint), and on a genuine change reloads the bundle and
highlights what changed for five seconds; a byte-identical recompile shows a determinism note with no
highlight, and a changed-but-undiffable apply — the build's one review must-fix — now renders that
honestly too, instead of defaulting into the no-op branch. **The Grafana embed (S7) is open**,
deliberately the last slice; the plugin's Track C imprints screen is still untouched, and whether it
retires at S7 is the one decision the work order holds rather than takes. *(Since ruled, 2026-09-11: it
retires at S7, with "Deploy as twin" kept as a slim plugin action — work order §8.1.)* **S5.1.a is BUILT
(2026-09-15)** — editing is selection-driven (§7, §8), and the cast publishes its segment roster (§10)
— and **S5.1.b–c are BUILT (2026-09-16)**: a gesture may stage a batch, a segment can be cloned with
its hosts (by drag, keyboard or tap) and placed as a pin, and a fresh segment can be born with
generated hosts whose listen set comes from the persona library (§4, §8). **S5.1.d–e are BUILT
(2026-09-16) too, completing S5.1:** pending work is drawn at both levels, the change list counts
gestures and undoes them whole, and a per-imprint **draft** keeps unsaved edits across a reload and
every way out of an imprint (GAP-094 closed). **S7 is BUILT too (2026-09-16)** — the Grafana panel
frames this SPA at its own origin and the plugin's imprints screen is retired with it (D5; §9) —
so **every slice of the Console work order is built**. What is left is operator-facing rather than
structural: the level-0 order's named follow-up (**column wrapping**), the apply-proof follow-ups
(GAP-087..GAP-090), and the things only a live Grafana can confirm about the embed
(`docs/WORK_ORDER_console_canvas.md` §21.5).
