# The axRange Console

**One app for the range** (`docs/reference/ui-consolidation/00-design-ruling.md`):
open it and see what is deployed, on which cluster, what each twin looks like,
which personas run on which host — and change one host's personas without
re-applying the whole twin. It is **run-time first**; the design-time
assertions canvas lives on as its **Design** area.

| Area | URL | Answers |
|---|---|---|
| **Range** (home) | `/`, `/range` | Every standing twin, grouped by cluster: status, hosts, personas, taps / traffic / exercise, age; each cluster's live reachability. |
| **Twin** | `/twin/<deploy_id>` | A map (Internet → routers → segment groups → segments; drill a segment to its hosts) and a filterable, sortable host table, sharing selection and the URL. Taps, destroy. |
| host panel | `/twin/<id>/hosts/<host>` | Facts, NICs, the personas it runs and what each sends, recorded status and last error, how to log in, live health on request. |
| persona change | `/twin/<id>/hosts/<host>/personas` | Add / remove / swap personas (picker filtered to the host's OS), optional role → **Preview** → **Apply to this host** → progress → result with the post-start heartbeat. Export the running profile. |
| **Catalog** | `/catalog[/twins\|scenarios/<id>]` | Every twin and scenario with its intended cluster; validate/plan where the API supports it; the one guarded deploy (pick → preview → confirm naming cluster and VM count → progress). |
| **Design** | `/design/...` | The assertions canvas (below), unchanged in function, with a glossary. |
| **Telemetry** | `/telemetry` | Links to the Grafana capture-health, Suricata-alerts and attack-coverage dashboards. |

Twin-page query: `?target=<cluster target>` (when not the default),
`?segment=<net>`, `?q=<filter>`, `?sort=`/`?dir=`. The grammar is pure and
round-trip tested (`model/appRoutes.ts`), as the Design grammar is (`model/zoom.ts`).

**Protected twins** (the server's protected-deploy list) carry a *Protected*
badge on the Range card, the Twin header and the host panel; persona changes and
destroy on them stay disabled until the operator overrides protection for that
one change by typing the deploy id (sent as `override_protection=<id>`; a 423 is
said in words). A persona change sends the previewed plan's `fingerprint` as
`expect_plan`: if the twin moved, the fresh plan is shown with what changed and
needs a new confirm, and the result is rendered from the plan the server
executed and its recorded job — a stop that failed reads "could not stop X — it
may still be running", never "Applied".

**Rules every run-time screen keeps** (ruling §2): recorded (state store) and
live (cluster, with its age) are always labelled apart — live is only ever
fetched when asked; unknown is shown as unknown, never 0; every empty view says
why; every write is preview → confirm → progress → result, and destroy needs the
deploy id typed back. One model draws a deployed twin: `GET /api/deploys/{id}/hosts`
(`model/deployed.ts`) through `layout/deployed.ts`, which reuses this package's
layout engine (sites, grid slots, column sizes, band stacking). The API it reads
is `docs/reference/ui-consolidation/04-api-contract.md`.

## Quick start

```bash
cd o2/console
npm ci
npm run build                    # -> o2/console/dist  (NOT while a server is serving it: LES-315)

# co-served beside the API (one origin, no CORS, no proxy):
axrange serve --spa-dir o2/console/dist --twins-dir twins --imprints-dir twins
#   add --targets <file> for more than one cluster (adapter.targets.example.yaml)
#   -> http://127.0.0.1:8080/  and the API at /api/*

# or, for UI work, the dev server with hot reload:
npm run dev                      # Vite on 127.0.0.1:5173, proxying /api to :8080
AXRANGE_DEV_API=http://otherhost:8080 npm run dev    # proxy somewhere else
```

`npm run typecheck` (strict `tsc --noEmit`), `npm run test` (Vitest). Both are
**clean from zero** in this package: LES-042's tolerated ~118-error typecheck
baseline belongs to the Grafana plugin and is not inherited here. The Grafana
control-panel plugin is **deprecated** by this app (ruling §4); nothing here
imports from it. Grafana stays for telemetry only.

## Layout

```
o2/console/
  src/
    main.tsx routes.tsx                # mount; the router + providers (one catch-all)
    api/   client.ts                   # fetch wrapper (makeRequester), base URL, token, typed errors
           runtime.ts runtimeTypes.ts  # the run-time client + its DTO mirrors (04 contract)
           runtimeContext.ts runtimeHooks.ts   # provided client; useRange/useHosts/useOnDemand/useNow
           types.ts context.ts hooks.ts        # the Design client's DTOs, context and hooks
    areas/ shell/     AppFrame.tsx writeAccess.tsx parts.tsx   # nav, cluster, token prompt, shared bits
           range/     RangePage.tsx
           twin/      TwinPage.tsx TwinMap.tsx HostTable.tsx HostPanel.tsx
                      PersonaEditor.tsx DestroyDialog.tsx TapsSection.tsx
           catalog/   CatalogPage.tsx
           telemetry/ TelemetryPage.tsx
           design/    DesignBar.tsx    # the Design area's frame + glossary
    model/ appRoutes.ts deployed.ts personaChange.ts deployFlow.ts format.ts   # run-time, pure
           keys.ts evidence.ts roles.ts roleListen.generated.ts graph.ts budgets.ts
           search.ts zoom.ts changes.ts                                        # Design, pure
    layout/ deployed.ts                # the deployed twin's map (levels 0 and 1)
           geometry.ts tiers.ts sites.ts level0.ts level1.ts matrix.ts pins.ts
           trunk.ts infra.ts arcs.ts columns.ts added.ts scale.ts plane.ts viewport.ts
    edit/  store.ts records.ts allocate.ts dependents.ts batch.ts clone.ts gestures.ts draft.ts
    theme/ tokens.css shell.css app.css   # palette (light/dark); Design shell; the app frame + areas
    App.tsx views/                     # the Design area: three-pane shell and its views
  test/    *.test.ts(x), helpers/runtime.ts (typed run-time fake),
           fixtures/corp-twin-hosts.json (the real 106-host corp-twin in the /hosts shape)
```

`model/` and `edit/` know nothing about React or the API; `api/` knows nothing
about layout; `views/` and `areas/` compose them.

## The Design area (the assertions canvas)

The canvas displays a cast twin's discovered structure at three levels and lets
an operator correct it as **assertion records**, save them, apply them
(recompile the cast) and refresh (`docs/design/14-operator-assertions.md` §4,
`docs/design/15-console-canvas.md`). **It edits the imprint, never the deployed
twin** — a running twin picks a change up only when it is redeployed; a running
host's personas are changed from the Twin page instead. Its build history (S2–S7,
R1–R7, S5.1.a–e) is recorded in `docs/WORK_ORDER_console_canvas.md`,
`docs/WORK_ORDER_console_level0_rendering.md` and `docs/CHANGELOG.md`.

Consolidation changes (2026-09-29): re-rooted under `/design`; the design-time
count formerly labelled "live" reads **would deploy**; **Apply** is disabled with
the server's own reason (`can_apply` / `apply_blocked_reason` on the imprint's
catalog entry; a 503 is remembered beside the button); a new assertions file is
seeded with `source: operator: console` and Save says what it still needs; the
canvas header carries the Design heading and a **Glossary** (imprint, cast,
assertion, ghost, occupancy, evidence classes, conduit) — one header, not two.

**A bundle with no topology export still draws** (design/16 rule 1): its
DECLARED design, from `GET .../cast`'s segment, node and router rosters
(`model/declared.ts`), through the same layout engine and zoom levels, every
element in the single class `declared` (never one of the six evidence classes).
One plain line says what it is; a hand-authored twin's Actions panel says to edit
its `te.yaml` and redeploy, and offers no assertion gestures. A design that is
deployed links to its live twin, and the Twin page links back ("Design").

## Design area: the URL is the zoom

| route | level |
|---|---|
| `/design` | the imprint catalog |
| `/design/<id>` | 0 — the network |
| `/design/<id>/segments/<cidr>` | 1 — the segment |
| `/design/<id>/hosts/<key>` | 2 — the host (flat form) |
| `/design/<id>/segments/<cidr>/hosts/<key>` | 2 — the host, with its segment context (`15` §3's spelling) |
| `/design/<id>/matrix` | the adjacency of the current scope |

The pre-consolidation spelling `/imprints/...` redirects to the same place under
`/design/...` (query kept, history replaced).

Two mechanical spellings, taken in the work order because `15` §3/§7's notation
is not directly expressible in a URL:

* **a CIDR's prefix length is written with a hyphen** —
  `/segments/10.80.20.0-24`, never `%2F` (some proxies normalize or reject an
  encoded slash, and these URLs are meant to be typed and pasted);
* **`?occupancy<0.8` is spelled `?occupancy_lt=0.8`**.

The `<id>` in every route is the **canonical** imprint id — the id the API is
keyed on (the bundle directory name = `twin_config.id` = the id in the page
URL). Drill-down (a tile click, an outline link, a search hit) builds every
route off that canonical id, threaded in from the URL — **never** a
producer-baked `imprint_id`/`pit_id` carried in the topology export (those are
display/provenance only). See `docs/WORK_ORDER_console_canvas.md` §14.6 and
LES-263.

Filters are `?ghosts=1`, `?class=<one of the six>`, `?occupancy_lt=<0..1>`;
`?view=matrix` is accepted as an alias and normalized to the `/matrix` path. A
value that cannot be used is **reported in the inspector**, never silently
dropped. Escape goes up a level, the breadcrumb is the route, the back button
works — because none of it is hidden in a store. (Since S5.1.a, Escape first
clears an in-place selection and returns focus to its opener, and it will not
leave an imprint that holds unsaved edits; a selection itself is transient
state, not part of the URL, and lasts until the pathname changes.)

## Configuration

The SPA's only build-time configuration is the **API base URL**, and in the normal case
it has no value: the app calls same-origin `/api/...`, which works both
co-served behind `axrange serve --spa-dir` and under `npm run dev`, whose
proxy forwards it. No imprint id, catalog, palette or cluster fact is compiled
in.

Two variables exist and they are **not interchangeable**:

| variable | read by | effect |
|---|---|---|
| `AXRANGE_DEV_API` | `vite.config.ts`, at config time | the dev server's **proxy target** (default `http://127.0.0.1:8080`). Never reaches the browser; the app still calls same-origin `/api`. |
| `VITE_AXRANGE_API` | `src/api/client.ts`, **compiled into the bundle** | an **absolute base URL** that bypasses the proxy and makes the browser talk cross-origin. The API installs **no CORS middleware**, so this only works behind a deployment that adds its own. |

They were one variable at first, which was a bug: Vite's `loadEnv` lifts every
`VITE_`-prefixed process variable into `import.meta.env`, so setting the proxy
target also compiled it in as the client's base URL and every dev request went
cross-origin past the proxy it was meant to configure.

The dev server binds **`127.0.0.1`** explicitly (`server.host`): Vite 8
defaults to `localhost`, which resolves to `[::1]` on this host, so
`curl http://127.0.0.1:5173` failed while a browser worked.

The **write token** is supplied at runtime — from the header's *Write token*
control, or from the prompt a write opens when the server answers 401
(`areas/shell/writeAccess.tsx`) — lives in a module variable for the life of
the tab (`setWriteToken()` in `api/client.ts`), and is **never** written to
`localStorage`, `sessionStorage` or a cookie — a reload drops it. A deployment that fronts the API with a same-origin session or
Grafana's datasource proxy supplies nothing and the writes still work, which is
why the header is omitted rather than sent empty. Reads carry no token: they
are open on a trusted bind.

## The evidence palette

`theme/tokens.css` carries the six classes in **precedence order** (`14` §2)
plus the two editing states, in light and dark, switched by
`prefers-color-scheme` and pinnable with `<html data-theme="light|dark">`:

| class | encoding |
|---|---|
| `asserted` | orange, ringed |
| `imported` | orange, hatched |
| `observed` | aqua, solid |
| `referenced` | aqua, hollow |
| `imputed` | violet |
| `compiler` | grey — **never authorable** |
| *pending* / *saved* | sky, hatched / sky, outlined |

The aqua/orange pair is the same source-vs-twin pair the shipped fidelity
report already uses (`reports/chasing-the-golden-twin.html`), so the two
surfaces read as one product. Every class also carries a **fill style**, so
`observed` and `referenced` stay distinguishable in monochrome and for a
colour-blind operator. `15` §12 leaves the exact hues open for a `dataviz` pass
before S3's first screen; this is that pass's starting point, not its verdict.

## §12.6 — how a twin-side removal joins back to the authored key

**The residual S1 handed to S2** (`docs/WORK_ORDER_console_canvas.md` §12.6):
`GET .../diff`'s twin side is keyed by the **twin's own node ids**, so a
`suppress` an operator keyed on `address:10.1.10.222` shows up there as
`generic-endpoint-10-1-1-core` disappearing. The canvas needs the authored key
to mark that edit *applied*.

**Decision: (b), server-side — but as a KEY-SET delta, not a per-removal
`authored_key` column.** `ImprintDiffDTO` gains one field:

```jsonc
"suppressed_keys": { "added": ["address:10.1.10.222"], "removed": [], "changed": [] }
```

computed by `recompile.suppressed_key_delta` from the `population.suppressed`
accounting **both** snapshots already hold. Why this shape:

* **A per-removal column would be a guess.** The sidecar's suppressed rows are
  `{key, note, placed, matched}` — they do **not** name the twin node a
  suppression removed — and the twin's group ids are minted by *aximprint's*
  grouping rule (role × segment × occupancy rung), which axRange must not
  reimplement to infer a mapping. With two suppressions and three removals (one
  of them the deploy gate's doing) an `authored_key` column would attribute the
  wrong key to the wrong host, confidently. The set delta is what the data
  supports, and it is exactly what the edit store needs.
* **It keeps the join in one place** for every consumer, which was the
  recommendation's actual argument, and it costs one DTO field on a route the
  SPA already calls after an apply.
* **`null` ≠ empty**: the field is `null`, with the reason in `warnings`, when
  either side carries no accounting (no sidecar, or the normalized shape) — the
  same contract `observed`/`twin` carry (LES-240).

The **durable** authority is unchanged and needs no diff at all:
`GET .../provenance`'s `population.suppressed[]` states which keys the compiled
cast honors, at any time, with no fingerprint to compare against. The SPA reads
both — `suppressedKeysFromDiff` for the post-apply highlight,
`suppressedKeysFromPopulation` on a plain load — and the latter separates a row
with `matched: false` (the record is in the file and matched **nothing**) from a
real suppression, so an ineffective edit is never reported as applied.

## Known behaviors of the co-serve (documented, not defects)

`axrange serve --spa-dir` serves this build with a history fallback so a deep
Console URL survives a reload (`axrange/api/app.py`). Four consequences worth
knowing:

- **Cache policy is two lines.** `index.html` (at `/`, at `/index.html` and at
  every fallback) is `Cache-Control: no-cache` -- it names the current content
  hashes and must be revalidated, or a browser holding yesterday's index asks
  for an asset the new build deleted. Everything under `/assets/` is
  `public, max-age=31536000, immutable`, which is safe precisely because Vite
  puts the content hash in the filename.
- **The fallback ignores `Accept`.** A non-browser client requesting an
  unknown path with `Accept: application/json` still receives `index.html`
  with a 200. Content negotiation was considered and skipped: the rule that
  actually protects a client is the `/api/` exclusion (an unknown API path is
  a JSON 404, never HTML), and `Accept`-based branching would make one URL
  answer differently to `curl` and to a browser -- harder to reason about
  than one rule.
- **A missing `.html` file falls back too.** `.html` is deliberately not in
  the asset-suffix list, so `GET /docs/manual.html` on a build with no such
  file renders the Console rather than 404ing. This build ships no secondary
  HTML pages, so the case does not arise today; if one is ever added, add
  `html` to `_SPA_ASSET_SUFFIXES` with it.
- **Sourcemaps are served.** `npm run build` emits `assets/*.js.map` and the
  static mount serves them, so anyone who can reach the Console can read the
  SPA's source. That is the intended trade for an internal, token-gated
  control plane (the bundle is the same source in a de-minified form); a
  public deployment should drop `build.sourcemap` or strip the maps at the
  proxy.

## The layout contract (S3)

`src/layout/` is pure TypeScript — no React, no DOM, no `react-flow` /
`d3-force` / `dagre`. `layout0(graph)` / `layout1(graph, cidr)` / `matrix0` /
`matrixSegment` take the joined `ConsoleGraph` and return coordinates, and three
properties are golden-tested (`test/layout.golden.test.ts`, §5):

- **Determinism** — the same input yields byte-identical coordinates.
- **Locality** — adding one segment to a site changes only that site's column;
  every other element's coordinates stay equal (the property the edit loop
  rests on).
- **Budget** — each level stays inside design-note §3's budget or collapses
  (the 27-host `generic-endpoint` band in `10.80.72.0/24` is the regression
  case, LES-185, and is in the fixture).

Goldens are produced from `test/fixtures/trimmed.ts` and regenerated ONLY by
`npm run layout:golden` (never on a plain `npm run test`). Beyond the goldens,
`test/layout.real.test.ts` runs the §3 count checks against the real
`twin-configs-cast/corpapcn-twin/` bundle (**18 drawn tiles of its 32 segments**
under R6's per-group budget — 32 again with every group expanded — 22 asserted
arcs, six infrastructure devices, the matrix-sum, the 49 unplaced + 596
non-local populations) — a hand fixture can't prove the layout works on what ships. Real
**data** is not the same as the real **configuration**, though: a test that
assembles the graph through a seam the app never uses is exercising a branch, not
the shipped view, and must say which (LES-311 — R4's budget tests are titled
`[no-roster construction]` for exactly that reason, with a separate `describe`
over the live shape).

## What the Design area does not yet do

Editing, save, apply and refresh all ship (S5 + S6) — seven gestures, three
visible states, saved to the assertions file, and Apply saves + recompiles +
reloads + highlights over the real `POST …/apply` endpoint. **Every level-0
rendering slice is now built** (`docs/WORK_ORDER_console_level0_rendering.md`
R1–R7, R6 landing 2026-09-15), and the **S5.1 editing amendment is complete**:
selection-driven editing (S5.1.a, 2026-09-15), clone-and-drag and generated
hosts (S5.1.b–c, 2026-09-16), and pending work drawn, counted per gesture,
undoable whole and kept as a draft (S5.1.d–e, 2026-09-16 — GAP-094 closed). What
remains is the **Grafana embed (S7)**, plus one follow-up the rendering order hands forward by
name: **column wrapping**, since the map still does not fit at a 1600 px window. The one proof that was outstanding is now **closed**:
`docs/WORK_ORDER_console_canvas.md` §13.2/§17.5 — that the **real** compiler
writes an authored key into the **new** sidecar on an apply — was proven
2026-09-11 by a real-compiler apply driven through the SPA's own HTTP calls
(five checks, a byte-identical second apply, six findings registered as
`docs/PRODUCT_GAPS.md` GAP-087..GAP-092). The
`dataviz` palette in `theme/tokens.css` is a starting point, not its verdict
(`15` §12). Pins ship (S4) but stay layout-only — a pin is never an assertion
and never touches the deployed twin.
